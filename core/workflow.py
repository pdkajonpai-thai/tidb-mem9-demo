"""LangGraph graph definitions: analysis_graph (no checkpointer), recommend_graph (TiDB checkpointer).

recommend_graph waits for a human in two places (interrupt).
- choose node: choosing the recommendation to execute
- inside the execute_order tool: order approval when the execution agent calls the engine API
On resume the paused node/tool reruns from the start, so no side effects are placed before an interrupt.
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
ANALYSIS_CONCURRENCY = 12      # 8 → 12: analyzing 40 posts 16s → 12s (measured 2026-11-05, no 429s)


# ---- Analysis graph ----

class AnalysisState(TypedDict, total=False):
    workspace: str                    # per-visitor workspace (core/workspace.py)
    signals: list[dict[str, Any]]
    catalog: dict[str, Any]
    drafts: Annotated[list[dict[str, Any]], operator.add]     # accumulated in parallel, one per source post
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


# ---- Recommendation/approval graph ----

class RecommendState(TypedDict, total=False):
    workspace: str                    # per-visitor workspace (core/workspace.py)
    user_id: int
    thread_id: str
    lang: str                         # ko / en: language of the sentences the AI generates in this thread
    profile: dict[str, Any]
    holdings: list[dict[str, Any]]
    exchanges: list[dict[str, Any]]
    memories: list[dict[str, Any]]
    memories_response: dict[str, Any]  # raw mem9 response of the memory lookup (for display, first 20)
    intent: str
    intent_reason: str
    candidates: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    attempts: int
    validation_errors: list[str]
    cards: list[dict[str, Any]]
    # execution phase: chosen recommendation → execution agent ⇄ execute_order tool (approval wait in the tool)
    selected: dict[str, Any] | None
    selected_at: str
    messages: Annotated[list[AnyMessage], add_messages]
    exec_tool_calls: int
    outcome: str                      # approved / rejected / failed
    command: dict[str, Any]           # ExecutionCommand shown on the approval screen and executed
    memory_job: str                   # mem9 memory update job after approve/reject (core/memory_jobs.py)
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
    # The checkpointer uses its own connection. LangGraph writes checkpoints concurrently with the next node
    # (durability="async"), and if it shared one connection (Lock) with the node's SQL, that SQL would wait
    # until the checkpoint write finished
    saver = TiDBSaver(Database())
    saver.setup()
    return build_recommend_graph(saver)
