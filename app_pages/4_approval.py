"""④ 승인 & 실행: 실행 agent가 호출한 execute_order tool의 승인 대기 → 승인/거절 → 엔진 실행 결과."""
import streamlit as st

from core.db import get_db
from core.engine_mock import route
from ui.common import (command_flow_panel, current_user, current_workspace, decide_order, localized, memory_job_panel,
                       thread_snapshot, waiting_threads)
from ui.i18n import columns, krw, t

user = current_user()
uid = user["id"]

orders = waiting_threads(uid, "order_approval")
if not orders:
    st.info(t("p4.none"))

for thread_id, snap, approval in orders:
    with st.container(border=True):
        title, note = localized([approval["title"], approval["note"]])
        st.markdown(f"#### {title}")
        st.markdown(t("p4.note", note=note))

        sim = approval["simulation"]
        with st.container(horizontal=True):
            st.metric(t("p4.route"), " + ".join(t("route." + r) for r in sim.get("routes", [])) or "-", border=True)
            st.metric(t("p4.position"), f"${sim['position_usd']:,.0f}" if sim["position_usd"] is not None else "-",
                      border=True)
            st.metric(t("p4.fee"), f"${sim['gas_usd']:,.2f}", border=True)
            st.metric(t("p4.yearly"), f"${sim['expected_yearly_usd']:,.0f}" if sim["expected_yearly_usd"] else "-",
                      border=True)

        if ov := approval.get("overseas"):
            gap = t("p4.exceeded") if ov["exceeded"] else t("p4.gap", gap=krw(ov["gap_krw"]))
            st.info(t("p4.overseas_body", latest=krw(ov["latest_krw"]), peak=krw(ov["peak_krw"]), gap=gap,
                      notice=t("overseas.notice")), title=t("p4.overseas_title"))

        cmd = approval["command"]
        st.dataframe(
            columns([{"step": i + 1, "action": s["action"], "amount": s["amount"], "input": s["from_asset"],
                      "output": s.get("to_asset") or "-", "exchange": s["exchange"], "location": s["chain"],
                      "route": t("route." + route(s["chain"])), "slippage": s["slippage_bps"]}
                     for i, s in enumerate(cmd["steps"])]),
            hide_index=True, alt=t("p4.steps_alt"))
        st.caption(t("p4.command_caption", id=cmd["command_id"], slip=cmd["max_slippage_bps"], exp=cmd["expires_at"]))
        command_flow_panel(snap.values, approval)      # 원문 → JSON → 엔진 명령 (기본 접힘)

        approve_col, reject_col = st.columns(2)
        with approve_col:
            with st.form(key=f"approve_{thread_id}", border=False):
                why = st.text_input(t("p4.approve_reason"), placeholder=t("p4.approve_reason_ph"))
                if st.form_submit_button(t("p4.approve"), type="primary", icon=":material/check:"):
                    decide_order(thread_id, {"action": "approve", "reason": why})
                    st.session_state["last_decision"] = thread_id
                    st.rerun()
        with reject_col:
            with st.form(key=f"reject_{thread_id}", border=False):
                reason = st.text_input(t("p4.reason"), placeholder=t("p4.reason_ph"))
                if st.form_submit_button(t("p4.reject"), icon=":material/close:"):
                    decide_order(thread_id, {"action": "reject", "reason": reason})
                    st.session_state["last_decision"] = thread_id
                    st.rerun()

# ---- 방금 처리한 결과 ----
if thread_id := st.session_state.get("last_decision"):
    state = thread_snapshot(thread_id).values
    if state.get("outcome"):
        with st.container(border=True):
            st.markdown(t("p4.just_done"))
            if order := state.get("order"):
                st.success(t("p4.filled", id=order["order_id"], status=order["status"], tx=order["tx_hash"][:18]))
                st.markdown(t("p4.engine_response"))
                st.json({"order_id": str(order["order_id"]), "status": order["status"], "tx_hash": order["tx_hash"],
                         "status_history": ["PENDING", "SUBMITTED", "CONFIRMED"]}, expanded=False)
            last = state["messages"][-1] if state.get("messages") else None
            if last is not None and getattr(last, "content", None):
                content = last.content if isinstance(last.content, str) else \
                    "".join(b.get("text", "") for b in last.content if isinstance(b, dict))
                st.markdown(t("p4.agent"))
                st.write(localized([content])[0])
            memory_job_panel(state.get("memory_job"))      # mem9 기억 정리 (기본 접힘, 백그라운드)

# ---- 최근 결정 이력 ----
history = get_db().query(
    """
    SELECT r.created_at, o.title, r.status, r.decided_at, e.status AS order_status, e.tx_hash
    FROM recommendations r
    JOIN opportunities o ON o.id = r.opportunity_id
    LEFT JOIN execution_orders e ON e.recommendation_id = r.id
    WHERE r.workspace = %s AND r.user_id = %s AND r.status IN ('APPROVED', 'REJECTED', 'FAILED')
    ORDER BY r.decided_at DESC LIMIT 10
    """, (current_workspace(), uid), label="승인: 결정 이력")
if history:
    st.markdown(t("p4.history"))
    titles = localized([h["title"] for h in history])
    st.dataframe(columns([{"decided_at": h["decided_at"], "recommendation": title,
                           "decision": t("status." + h["status"]), "order": h["order_status"] or "-",
                           "tx": (h["tx_hash"] or "")[:18]} for h, title in zip(history, titles)]),
                 hide_index=True, alt=t("p4.history_alt"))
