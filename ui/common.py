"""화면 공통 도구: 사용자 선택, 표시용 번역, 스레드 상태 읽기, 추천 카드, 그래프 실행 스트리밍, 명령 생성 과정.

화면 상태는 브라우저 세션이 아니라 TiDB 체크포인트에서 다시 읽는다.
새로고침·재접속·다른 기기에서도 대기 중인 추천과 주문이 그대로 보인다.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from langchain_core.utils.json import parse_partial_json
from langgraph.types import Command

from core import engine_mock, graph_runs, memory_jobs, translations, workflow
from core.audit import identity, log_event
from core.db import get_db
from core.recommender import MAX_SLIPPAGE_BPS, plain
from core.schemas import ExecutionCommand, Opportunity
from core.workspace import workspace_id
from ui.i18n import LANGS, lang, t

PAGE_RECOMMEND = "app_pages/3_recommendations.py"
PAGE_APPROVAL = "app_pages/4_approval.py"
STEP_NODES = ("recall_memory", "build_intent", "search_candidates", "recommend", "validate",
              "save_recommendations", "exec_agent", "tools")
MEMORY_ICONS = {"pinned": "📌", "insight": "💡", "session": "💬"}


# ---- 사용자 ----

@st.cache_data(show_spinner=False, ttl=300)
def users() -> list[dict]:
    return get_db().query("SELECT id, name, name_en, risk_profile, max_risk_level FROM users ORDER BY id",
                          label="화면: 사용자 목록")


def persona_name(user: dict) -> str:
    return user["name"] if lang() == "ko" else user.get("name_en") or user["name"]


def current_user_id() -> int:
    return st.session_state.get("user_id", 1)


def current_workspace() -> str:
    """로그인한 Google 계정의 작업 공간 (core/workspace.py)."""
    return workspace_id(identity()["email"] or "anonymous")


def current_user() -> dict:
    uid = current_user_id()
    return next((u for u in users() if u["id"] == uid), {"id": uid, "name": f"User {uid}", "name_en": f"User {uid}"})


# ---- 표시용 번역 ----

@st.cache_data(show_spinner=False, max_entries=2000)
def _translate(texts: tuple[str, ...], target: str) -> list[str]:
    """TiDB 번역 캐시(core/translations.py) 위에 세션 간 메모리 캐시를 한 겹 더 둔다."""
    return translations.cached_translate(list(texts), target)


def localized(texts: list[str]) -> list[str]:
    """저장된 내용·다른 언어로 만든 결과를 현재 언어로 보여준다 (원본은 바꾸지 않는다)."""
    if not texts:
        return texts
    try:
        with st.spinner(t("common.translating")):   # 캐시에 있으면 0.5초 안에 끝나 표시되지 않는다
            return _translate(tuple(texts), lang())
    except Exception:
        return texts      # 번역 실패 시 원문 그대로 보여준다


def prepare_translations(texts: list[str]) -> None:
    """두 언어의 번역을 미리 만들어 캐시에 넣는다 (화면을 처음 열 때 기다리지 않도록)."""
    for target in LANGS:
        try:
            _translate(tuple(texts), target)
        except Exception:
            pass          # 미리 만들지 못해도 화면에서 다시 시도한다


def localized_one(text: str | None) -> str:
    return localized([text])[0] if text else (text or "")


MEMORY_SOURCES = ("onboarding", "learned", "history")


def memory_source(m: dict) -> str:
    """기억이 생긴 곳: 가입 시 선호 / 대화에서 배운 기억 / 결정·실행 기록 (화면 구분용)."""
    tags = set(m.get("tags") or [])
    if m.get("memory_type") == "insight" or "insight-fallback" in tags:
        return "learned"
    if "preference" in tags:
        return "onboarding"
    return "history"          # decision, execution, 그 밖의 pinned


def memory_lines(memories: list[dict]) -> list[str]:
    shown = localized([m["content"] for m in memories])
    return [f"{MEMORY_ICONS.get(m.get('memory_type'), '•')} {text}" for m, text in zip(memories, shown)]


def localize_cards(cards: list[dict]) -> list[dict]:
    """카드의 제목·근거·기회 이름을 현재 언어로 (한 번의 번역 호출로 묶는다)."""
    texts = [x for c in cards for x in (c["headline"], c["rationale"], c["title"])]
    out = localized(texts)
    return [{**c, "headline": out[i * 3], "rationale": out[i * 3 + 1], "title": out[i * 3 + 2]}
            for i, c in enumerate(cards)]


# ---- 스레드 상태 ----

def thread_snapshot(thread_id: str):
    return workflow.get_recommend_graph().get_state(workflow.thread_config(thread_id))


def pending(snapshot) -> dict | None:
    """스레드가 사람을 기다리고 있으면 interrupt 값({"type": ...})을 돌려준다."""
    return snapshot.interrupts[0].value if snapshot and snapshot.interrupts else None


def latest_thread(user_id: int) -> str | None:
    rows = get_db().query(
        """SELECT thread_id FROM recommendations WHERE workspace = %s AND user_id = %s
           ORDER BY created_at DESC, `rank` LIMIT 1""",
        (current_workspace(), user_id), label="화면: 최근 추천 스레드")
    return rows[0]["thread_id"] if rows else None


def waiting_threads(user_id: int, kind: str) -> list[tuple[str, Any, dict]]:
    """kind 타입(choose_card / order_approval)으로 멈춰 있는 스레드 목록 (최근 것부터)."""
    rows = get_db().query(
        """
        SELECT thread_id, MAX(created_at) AS created_at FROM recommendations
        WHERE workspace = %s AND user_id = %s AND status = 'AWAITING_APPROVAL'
        GROUP BY thread_id ORDER BY created_at DESC LIMIT 5
        """, (current_workspace(), user_id), label="화면: 대기 중인 스레드")
    found = []
    for r in rows:
        snap = thread_snapshot(r["thread_id"])
        value = pending(snap)
        if value and value.get("type") == kind:
            found.append((r["thread_id"], snap, value))
    return found


# ---- 추천 카드 ----

def card_meta(card: dict) -> str:
    apy = t("card.apy", apy=f"{card['expected_apy']:g}") if card.get("expected_apy") is not None else t("card.apy_none")
    where = t("card.account", loc=card["chain"]) if engine_mock.route(card["chain"]) == engine_mock.EXCHANGE_API else card["chain"]
    return f"{card['protocol']} · {where} · {t('action.' + card['action_type'])} · {apy} · {t('card.risk', r=card['risk_level'])}"


def render_card(card: dict, *, button_key: str | None = None) -> bool:
    """추천 카드를 그리고, 버튼을 누르면 True. card는 localize_cards()를 거친 값."""
    with st.container(border=True):
        st.markdown(f"**#{card['rank']} {card['headline']}**")
        st.caption(card_meta(card))
        st.write(card["rationale"])
        if card.get("memory_refs"):
            with st.expander(t("card.memories", n=len(card["memory_refs"]))):
                for line in memory_lines(card["memory_refs"]):
                    st.markdown(f"- {line}")
        with st.expander(t("card.steps")):
            st.dataframe(card["proposed_steps"], hide_index=True, alt=t("card.steps_alt", title=card["title"]))
        if button_key:
            return st.button(t("card.request"), key=button_key, type="primary")
    return False


# ---- 그래프 실행 (스트리밍) ----

def _blocks(chunk) -> list[dict]:
    content = chunk.content
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def _tool_updates(update) -> list[dict]:
    """ToolNode의 updates 값(dict 또는 Command 결과 목록)을 dict 목록으로."""
    items = update if isinstance(update, list) else [update]
    return [u if isinstance(u, dict) else getattr(u, "update", None) or {} for u in items]


def stream_graph(graph_input, thread_id: str, status, *, phase: str, show_cards: bool = False) -> None:
    """추천 그래프를 실행하며 진행 단계, AI 검토 메모, 만들어지는 카드를 status 안에 보여준다.

    승인·거절 뒤에는 엔진 결과를 tool이 끝나는 즉시 보여주고, agent의 결과 요약은 그 아래에 스트리밍한다.
    노드별 걸린 시간은 graph_runs에 남아 Behind the scenes의 LangGraph 탭에 보인다.
    """
    graph = workflow.get_recommend_graph()
    memo_box = status.empty()
    cards_box = status.empty()
    agent_box = None
    memo, body, summary = "", "", ""
    for mode, payload in graph_runs.stream_timed(graph, graph_input, workflow.thread_config(thread_id),
                                                 phase=phase, stream_mode=("updates", "messages")):
        if mode == "updates":
            for node, update in payload.items():
                if node not in STEP_NODES:
                    continue
                status.write(f"✓ {t('step.' + node)}")
                if node == "build_intent" and update:
                    status.caption(t("stream.intent", x=update["intent"]))
                if node == "search_candidates" and update:
                    status.caption(t("stream.candidates", n=len(update["candidates"])))
                if node == "exec_agent" and update:
                    for call in getattr(update["messages"][-1], "tool_calls", None) or []:
                        status.code(f"{call['name']}(note={call['args'].get('note')!r}, "
                                    f"steps={len(call['args'].get('steps', []))})", language="python")
                if node == "tools" and update:
                    for u in _tool_updates(update):
                        if order := u.get("order"):
                            status.success(t("p4.filled", id=order["order_id"], status=order["status"],
                                             tx=order["tx_hash"][:18]))
                        elif u.get("outcome") == "rejected":
                            status.info(t("stream.rejected"))
                        if u.get("outcome"):
                            agent_box = status.empty()
            continue
        chunk, meta = payload
        if meta.get("langgraph_node") == "exec_agent" and agent_box is not None:
            summary += "".join(b.get("text", "") for b in _blocks(chunk) if b.get("type") == "text")
            if summary:
                agent_box.markdown(f"{t('p4.agent')}  \n{summary}")
            continue
        if meta.get("langgraph_node") != "recommend":
            continue
        for b in _blocks(chunk):
            if b.get("type") == "thinking" and b.get("thinking"):
                memo += b["thinking"]
                memo_box.caption(t("stream.memo", x=memo[-300:]))
            elif b.get("type") == "text" and b.get("text") and show_cards:
                body += b["text"]
                parsed = parse_partial_json(body) if body.lstrip().startswith("{") else None
                drafts = (parsed or {}).get("cards") or []
                lines = [f"**#{d.get('rank', '?')} {d.get('headline', '')}**  \n{d.get('rationale', '')}"
                         for d in drafts if d.get("headline")]
                if lines:
                    cards_box.markdown("\n\n".join(lines))


def start_recommendation(user_id: int) -> str:
    workspace = current_workspace()
    thread_id = workflow.new_thread_id(workspace)
    log_event("llm:recommend", {"persona": user_id, "thread_id": thread_id, "lang": lang()})
    with st.status(t("status.building"), expanded=True) as status:
        stream_graph({"workspace": workspace, "user_id": user_id, "thread_id": thread_id, "lang": lang()},
                     thread_id, status, phase="recommend", show_cards=True)
        status.update(label=t("status.built"), state="complete", expanded=False)
    return thread_id


def request_execution(thread_id: str, recommendation_id: int) -> None:
    log_event("llm:execute_request", {"thread_id": thread_id, "recommendation_id": str(recommendation_id)})
    with st.status(t("status.calling_tool"), expanded=True) as status:
        stream_graph(Command(resume={"recommendation_id": recommendation_id}, update={"lang": lang()}), thread_id, status,
                     phase="execute")
        status.update(label=t("status.waiting_approval"), state="complete", expanded=False)


def decide_order(thread_id: str, decision: dict) -> None:
    log_event("llm:decision", {"thread_id": thread_id, "action": decision["action"],
                               "reason": decision.get("reason", "")})
    label = t("status.executing") if decision["action"] == "approve" else t("status.recording")
    with st.status(label, expanded=True) as status:
        stream_graph(Command(resume=decision, update={"lang": lang()}), thread_id, status, phase="decide")
        status.update(label=t("status.done"), state="complete", expanded=False)


# ---- 승인·거절 뒤 mem9 기억 정리 (백그라운드 작업) ----

STEP_ICONS = {"done": "✓", "running": "⏳", "warn": "⚠"}


def _step_line(step: dict) -> str:
    key, state, d = step["key"], step["state"], step["detail"]
    if key == "sent":
        status = "202 Accepted" if d.get("status") == 202 else str(d.get("status"))
        title, detail = t("job.sent"), t("job.sent_detail", status=status, ms=d.get("ms"))
    elif key == "extracting" and state == "running":
        title, detail = t("job.extracting_running"), t("job.extracting_detail", n=d.get("polls", 0))
    elif key == "extracting" and state == "warn":
        title, detail = t("job.extracting_warn", s=d.get("secs")), ""
    elif key == "extracting":
        title, detail = t("job.extracting_done"), t("job.secs", s=d.get("secs"))
    elif key in ("learned", "fallback"):
        icon = "💡" if key == "learned" else "📌"
        items = localized(d.get("items", []))
        title = t("job.learned", n=len(items)) if key == "learned" else t("job.fallback")
        detail = " / ".join(f"{icon} {x}" for x in items)
    else:                                  # decision_saved, execution_saved
        title, detail = t("job." + key), "📌 " + localized_one(d.get("text"))
    line = f"`+{step['at']:.1f}s` {STEP_ICONS[state]} **{title}**"
    return f"{line} · {detail}" if detail else line


def _memory_job_body(job_id: str, snap: dict) -> None:
    learned = sum(e["kind"] in ("insight", "fallback") for e in snap["events"])
    if snap["error"]:
        label = t("job.title_failed")
    elif snap["running"]:
        label = t("job.title_running", s=f"{snap['elapsed']:.0f}")
    elif any(s["key"] == "sent" for s in snap["steps"]):
        label = t("job.title_done", n=learned, s=f"{snap['elapsed']:.1f}")
    else:
        label = t("job.title_recorded", s=f"{snap['elapsed']:.1f}")
    # 라벨이 매초 바뀌어도 펼침 상태가 유지되도록 key로 고정한다.
    # on_change를 함께 주면 라벨이 바뀔 때 접혀 버리므로 주지 않는다 (2026-10-30 확인)
    with st.expander(label, icon=":material/psychology:", key=f"memjob_{job_id}"):
        st.caption(t("job.intro"))
        st.markdown("  \n".join(_step_line(s) for s in snap["steps"]))
        if snap["error"]:
            st.error(t("job.error", e=snap["error"]))
        elif not snap["running"] and learned:
            st.caption(t("job.to_dashboard"))


@st.fragment(run_every=1)
def _memory_job_live(job_id: str) -> None:
    job = memory_jobs.get(job_id)
    snap = job.snapshot() if job else None
    if snap is None or not snap["running"]:
        st.rerun()                         # 끝나면 전체를 다시 그려 1초 갱신을 멈춘다
    _memory_job_body(job_id, snap)


def memory_job_panel(job_id: str | None) -> None:
    """승인·거절 뒤 mem9가 뒤에서 하는 일. 기본은 접혀 있고, 진행 중일 때만 1초마다 갱신한다."""
    job = memory_jobs.get(job_id)
    if job is None:                        # 앱 재시작 등으로 작업 정보가 없으면 표시하지 않는다
        return
    snap = job.snapshot()
    if snap["running"]:
        _memory_job_live(job_id)
    else:
        _memory_job_body(job_id, snap)


@st.fragment(run_every=1)
def memory_updating_note(app_id: str) -> None:
    """대시보드: 이 기억 공간의 정리가 진행 중이면 '정리 중…'을 띄우고, 끝나면 화면을 다시 그린다."""
    if memory_jobs.pending(app_id):
        st.caption(t("mem.updating"))
    else:
        st.rerun()



# ---- 승인 화면: 투자 정보가 엔진 명령이 되기까지 (Structured Output) ----

ORIGINS = {"llm": ("violet", ":material/auto_awesome:"), "app": ("blue", ":material/verified:"),
           "engine": ("green", ":material/settings_suggest:")}


def _flow_step(n: int, key: str, origin: str, caption: str) -> None:
    color, icon = ORIGINS[origin]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{n}. {t(key)}**")
        st.badge(t("p4.by_" + origin), icon=icon, color=color)
    st.caption(caption)


def command_flow_panel(values: dict, approval: dict) -> None:
    """원문 → Opportunity → CommandDraft → 앱 검증 → tool 호출 → ExecutionCommand → 엔진 시뮬레이션을 JSON으로 보여준다.

    LLM이 만든 값은 그대로, 앱이 채운 값은 채운 그대로 보여 '어디까지가 AI이고 어디부터가 앱인지'를 드러낸다.
    """
    card = values["selected"]
    rows = get_db().query(
        """SELECT s.id AS signal_id, s.source, s.author, s.posted_at, s.content,
                  o.title, o.asset, o.chain, p.name AS protocol, o.action_type, o.expected_apy, o.risk_level,
                  o.horizon, o.confidence, o.summary, o.rationale
           FROM opportunities o
           JOIN protocols p ON p.id = o.protocol_id
           JOIN raw_signals s ON s.id = o.signal_id
           WHERE o.id = %s""", (card["opportunity_id"],), label="승인: 원문·기회 조회")
    row = plain(rows[0]) if rows else None
    calls = [c for m in values.get("messages", []) for c in getattr(m, "tool_calls", None) or []]
    command = approval["command"]

    with st.expander(t("p4.flow"), icon=":material/data_object:"):
        st.caption(t("p4.flow_intro"))
        if row:
            _flow_step(1, "p4.flow_1", "app", t("p4.flow_1_caption", id=row["signal_id"], source=row["source"],
                                                  author=row["author"]))
            st.json({k: row[k] for k in ("source", "author", "posted_at", "content")})
            _flow_step(2, "p4.flow_2", "llm", t("p4.flow_2_caption"))
            st.json({k: row[k] for k in Opportunity.model_fields}, expanded=1)
        _flow_step(3, "p4.flow_3", "llm", t("p4.flow_3_caption", n=values.get("attempts", 1)))
        st.json({"steps": card["proposed_steps"],
                 "max_slippage_bps": max((s["slippage_bps"] for s in card["proposed_steps"]), default=0)})
        _flow_step(4, "p4.flow_4", "app", t("p4.flow_4_caption"))
        st.markdown(t("p4.flow_checks", max=MAX_SLIPPAGE_BPS)
                    + ("\n" + t("p4.flow_check_lp") if card.get("action_type") == "lp" else ""))
        if calls:
            _flow_step(5, "p4.flow_5", "llm", t("p4.flow_5_caption", n=values.get("exec_tool_calls", len(calls))))
            st.json({"tool": calls[-1]["name"], "args": calls[-1]["args"]})
            st.caption(t("p4.tool_caption"))
        _flow_step(6, "p4.flow_6", "app", t("p4.flow_6_caption"))
        st.json(command)
        st.caption(t("p4.flow_schema"))
        st.json(ExecutionCommand.model_json_schema(), expanded=False)
        _flow_step(7, "p4.flow_7", "engine", t("p4.flow_7_caption"))
        st.json(approval["simulation"], expanded=1)
