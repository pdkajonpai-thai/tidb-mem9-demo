"""Shared UI helpers: user selection, display translation, thread state reads, recommendation cards, graph run
streaming, command generation steps.

Page state is re-read from TiDB checkpoints, not from the browser session.
Pending recommendations and orders stay visible after a refresh, a reconnect, or on another device.
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


# ---- User ----

@st.cache_data(show_spinner=False, ttl=300)
def users() -> list[dict]:
    return get_db().query("SELECT id, name, name_en, risk_profile, max_risk_level FROM users ORDER BY id",
                          label="화면: 사용자 목록")


def persona_name(user: dict) -> str:
    return user["name"] if lang() == "ko" else user.get("name_en") or user["name"]


def current_user_id() -> int:
    return st.session_state.get("user_id", 1)


def current_workspace() -> str:
    """Workspace of the logged-in Google account (core/workspace.py)."""
    return workspace_id(identity()["email"] or "anonymous")


def current_user() -> dict:
    uid = current_user_id()
    return next((u for u in users() if u["id"] == uid), {"id": uid, "name": f"User {uid}", "name_en": f"User {uid}"})


# ---- Display translation ----

@st.cache_data(show_spinner=False, max_entries=2000)
def _translate(texts: tuple[str, ...], target: str) -> list[str]:
    """Adds a cross-session in-memory cache layer on top of the TiDB translation cache (core/translations.py)."""
    return translations.cached_translate(list(texts), target)


def localized(texts: list[str]) -> list[str]:
    """Shows stored content and other-language results in the current language (the original is unchanged)."""
    if not texts:
        return texts
    try:
        with st.spinner(t("common.translating")):   # a cache hit finishes within 0.5s, so this is not shown
            return _translate(tuple(texts), lang())
    except Exception:
        return texts      # on translation failure, show the original text


def prepare_translations(texts: list[str]) -> None:
    """Pre-builds translations for both languages into the cache (so the first page open does not wait)."""
    for target in LANGS:
        try:
            _translate(tuple(texts), target)
        except Exception:
            pass          # if pre-building fails, the page retries


def localized_one(text: str | None) -> str:
    return localized([text])[0] if text else (text or "")


MEMORY_SOURCES = ("onboarding", "learned", "history")


def memory_source(m: dict) -> str:
    """Memory origin: sign-up preference / learned from conversation / decision or execution record (for UI)."""
    tags = set(m.get("tags") or [])
    if m.get("memory_type") == "insight" or "insight-fallback" in tags:
        return "learned"
    if "preference" in tags:
        return "onboarding"
    return "history"          # decision, execution, other pinned


def memory_lines(memories: list[dict]) -> list[str]:
    shown = localized([m["content"] for m in memories])
    return [f"{MEMORY_ICONS.get(m.get('memory_type'), '•')} {text}" for m, text in zip(memories, shown)]


def localize_cards(cards: list[dict]) -> list[dict]:
    """Card titles, rationale, and opportunity names in the current language (batched into one translation call)."""
    texts = [x for c in cards for x in (c["headline"], c["rationale"], c["title"])]
    out = localized(texts)
    return [{**c, "headline": out[i * 3], "rationale": out[i * 3 + 1], "title": out[i * 3 + 2]}
            for i, c in enumerate(cards)]


# ---- Thread state ----

def thread_snapshot(thread_id: str):
    return workflow.get_recommend_graph().get_state(workflow.thread_config(thread_id))


def pending(snapshot) -> dict | None:
    """Returns the interrupt value ({"type": ...}) if the thread is waiting for a human."""
    return snapshot.interrupts[0].value if snapshot and snapshot.interrupts else None


def latest_thread(user_id: int) -> str | None:
    rows = get_db().query(
        """SELECT thread_id FROM recommendations WHERE workspace = %s AND user_id = %s
           ORDER BY created_at DESC, `rank` LIMIT 1""",
        (current_workspace(), user_id), label="화면: 최근 추천 스레드")
    return rows[0]["thread_id"] if rows else None


def waiting_threads(user_id: int, kind: str) -> list[tuple[str, Any, dict]]:
    """Threads paused on an interrupt of type kind (choose_card / order_approval), most recent first."""
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


# ---- Recommendation cards ----

def card_meta(card: dict) -> str:
    apy = t("card.apy", apy=f"{card['expected_apy']:g}") if card.get("expected_apy") is not None else t("card.apy_none")
    where = t("card.account", loc=card["chain"]) if engine_mock.route(card["chain"]) == engine_mock.EXCHANGE_API else card["chain"]
    return f"{card['protocol']} · {where} · {t('action.' + card['action_type'])} · {apy} · {t('card.risk', r=card['risk_level'])}"


def render_card(card: dict, *, button_key: str | None = None) -> bool:
    """Draws a recommendation card; True when its button is clicked. card has gone through localize_cards()."""
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


# ---- Graph run (streaming) ----

def _blocks(chunk) -> list[dict]:
    content = chunk.content
    return content if isinstance(content, list) else [{"type": "text", "text": content}]


def _tool_updates(update) -> list[dict]:
    """Turns a ToolNode updates value (a dict or a list of Command results) into a list of dicts."""
    items = update if isinstance(update, list) else [update]
    return [u if isinstance(u, dict) else getattr(u, "update", None) or {} for u in items]


def stream_graph(graph_input, thread_id: str, status, *, phase: str, show_cards: bool = False) -> None:
    """Runs the recommendation graph, showing progress, AI review notes, and cards as they are built in a status.

    After approve/reject, shows the engine result as soon as the tool finishes and streams the agent's
    summary below it.
    Per-node timing is stored in graph_runs and shown in the LangGraph tab of Behind the scenes.
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


# ---- mem9 memory update after approve/reject (background job) ----

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
    # Pin with a key so the expanded state survives the label changing every second.
    # Passing on_change too makes it collapse when the label changes, so it is omitted (verified 2026-10-30)
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
        st.rerun()                         # when done, redraw everything to stop the 1-second refresh
    _memory_job_body(job_id, snap)


def memory_job_panel(job_id: str | None) -> None:
    """What mem9 does behind the scenes after approve/reject. Collapsed by default; refreshes each second while running."""
    job = memory_jobs.get(job_id)
    if job is None:                        # show nothing if job info is gone (e.g. after an app restart)
        return
    snap = job.snapshot()
    if snap["running"]:
        _memory_job_live(job_id)
    else:
        _memory_job_body(job_id, snap)


@st.fragment(run_every=1)
def memory_updating_note(app_id: str) -> None:
    """Dashboard: shows '정리 중…' ("Updating…") while this memory space is being updated, and redraws when done."""
    if memory_jobs.pending(app_id):
        st.caption(t("mem.updating"))
    else:
        st.rerun()



# ---- Approval page: how investment info becomes an engine command (Structured Output) ----

ORIGINS = {"llm": ("violet", ":material/auto_awesome:"), "app": ("blue", ":material/verified:"),
           "engine": ("green", ":material/settings_suggest:")}


def _flow_step(n: int, key: str, origin: str, caption: str) -> None:
    color, icon = ORIGINS[origin]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown(f"**{n}. {t(key)}**")
        st.badge(t("p4.by_" + origin), icon=icon, color=color)
    st.caption(caption)


def command_flow_panel(values: dict, approval: dict) -> None:
    """Shows source text → Opportunity → CommandDraft → app check → tool call → ExecutionCommand → engine simulation
    as JSON.

    Values from the LLM and values filled in by the app are shown as-is, revealing 'where the AI ends and the app
    begins'.
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
