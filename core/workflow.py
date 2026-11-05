"""LangGraph 그래프 정의: analysis_graph(체크포인터 없음), recommend_graph(TiDB 체크포인터).

recommend_graph는 두 곳에서 사람을 기다린다 (interrupt).
- choose 노드: 실행할 추천 선택
- execute_order tool 안: 실행 agent가 엔진 API를 호출할 때 주문 승인
재개하면 멈췄던 노드·tool이 처음부터 다시 실행되므로, interrupt 앞에는 부수 효과를 두지 않는다.
"""
from __future__ import annotations

import operator
import uuid
from functools import lru_cache
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy, Send

from core import analyzer, recommender
from core.checkpoint_tidb import TiDBSaver
from core.db import Database
from core.workspace import thread_prefix

LLM_RETRY = RetryPolicy(max_attempts=3)
ANALYSIS_CONCURRENCY = 12      # 8 → 12: 원문 40건 분석 16초 → 12초 (2026-11-05 측정, 429 없음)


# ---- 분석 그래프 ----

class AnalysisState(TypedDict, total=False):
    workspace: str                    # 방문자별 작업 공간 (core/workspace.py)
    signals: list[dict[str, Any]]
    catalog: dict[str, Any]
    drafts: Annotated[list[dict[str, Any]], operator.add]     # 원문마다 병렬로 쌓임
    skipped: Annotated[list[dict[str, Any]], operator.add]
    opportunities: list[dict[str, Any]]
    saved: int


def fan_out_signals(state: AnalysisState):
    if not state["signals"]:
        return END
    return [Send("analyze_signal", {"signal": s, "catalog": state["catalog"]}) for s in state["signals"]]


def build_analysis_graph():
    g = StateGraph(AnalysisState)
    g.add_node("load_signals", analyzer.load_signals)
    g.add_node("analyze_signal", analyzer.analyze_signal, retry_policy=LLM_RETRY)
    g.add_node("map_entities", analyzer.map_entities)
    g.add_node("save_opportunities", analyzer.save_opportunities)
    g.add_edge(START, "load_signals")
    g.add_conditional_edges("load_signals", fan_out_signals, ["analyze_signal", END])
    g.add_edge("analyze_signal", "map_entities")
    g.add_edge("map_entities", "save_opportunities")
    g.add_edge("save_opportunities", END)
    return g.compile()


def run_analysis(workspace: str, **config) -> AnalysisState:
    return get_analysis_graph().invoke({"workspace": workspace},
                                       {"max_concurrency": ANALYSIS_CONCURRENCY, **config})


# ---- 추천·승인 그래프 ----

class RecommendState(TypedDict, total=False):
    workspace: str                    # 방문자별 작업 공간 (core/workspace.py)
    user_id: int
    thread_id: str
    lang: str                         # ko / en: 이 스레드에서 AI가 생성하는 문장의 언어
    profile: dict[str, Any]
    holdings: list[dict[str, Any]]
    exchanges: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    memories_response: dict[str, Any]  # 기억 조회 mem9 응답 원본 (화면 표시용, 앞 20개)
    intent: str
    intent_reason: str
    candidates: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    attempts: int
    validation_errors: list[str]
    cards: list[dict[str, Any]]
    # 실행 단계: 선택한 추천 → 실행 agent ⇄ execute_order tool (tool 안에서 승인 대기)
    selected: dict[str, Any] | None
    selected_at: str
    messages: Annotated[list[AnyMessage], add_messages]
    exec_tool_calls: int
    outcome: str                      # approved / rejected / failed
    command: dict[str, Any]           # 승인 화면에 보이고 실행된 ExecutionCommand
    memory_job: str                   # 승인·거절 뒤 mem9 기억 정리 작업 (core/memory_jobs.py)
    order: dict[str, Any]
    error: str


def build_recommend_graph(checkpointer):
    g = StateGraph(RecommendState)
    g.add_node("recall_memory", recommender.recall_memory)
    g.add_node("build_intent", recommender.build_intent, retry_policy=LLM_RETRY)
    g.add_node("search_candidates", recommender.search_candidates)
    g.add_node("recommend", recommender.recommend, retry_policy=LLM_RETRY)
    g.add_node("validate", recommender.validate)
    g.add_node("no_result", recommender.no_result)
    g.add_node("save_recommendations", recommender.save_recommendations)
    g.add_node("choose", recommender.choose)
    g.add_node("exec_agent", recommender.exec_agent, retry_policy=LLM_RETRY)
    g.add_node("tools", ToolNode(recommender.EXEC_TOOLS))
    g.add_node("finish", recommender.finish)

    g.add_edge(START, "recall_memory")
    g.add_edge("recall_memory", "build_intent")
    g.add_edge("build_intent", "search_candidates")
    g.add_edge("search_candidates", "recommend")
    g.add_edge("recommend", "validate")
    g.add_conditional_edges("validate", recommender.route_after_validate,
                            ["recommend", "save_recommendations", "no_result"])
    g.add_edge("no_result", END)
    g.add_edge("save_recommendations", "choose")
    g.add_conditional_edges("choose", recommender.route_after_choose, {"exec_agent": "exec_agent", "done": END})
    g.add_conditional_edges("exec_agent", recommender.route_after_exec_agent, ["tools", "finish"])
    g.add_edge("tools", "exec_agent")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def new_thread_id(workspace: str) -> str:
    return f"{thread_prefix(workspace)}{uuid.uuid4().hex[:12]}"


def thread_config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


@lru_cache
def get_analysis_graph():
    return build_analysis_graph()


@lru_cache
def get_recommend_graph():
    # 체크포인터는 연결을 따로 쓴다. LangGraph는 체크포인트를 다음 노드와 동시에(durability="async") 쓰는데,
    # 노드의 SQL과 한 연결(Lock)을 같이 쓰면 그 SQL이 체크포인트 쓰기가 끝날 때까지 기다린다
    saver = TiDBSaver(Database())
    saver.setup()
    return build_recommend_graph(saver)
