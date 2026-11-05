"""③ 개인화 추천: 기억 → 검색 의도 → 하이브리드 SQL → 추천 카드 (스트리밍). 카드에서 실행 요청."""
import streamlit as st

from core.db import get_db
from ui.common import (MEMORY_SOURCES, PAGE_APPROVAL, current_user, current_workspace, latest_thread,
                       localize_cards, localized, memory_lines, memory_source, pending, persona_name, render_card,
                       request_execution, start_recommendation, thread_snapshot, waiting_threads)
from ui.i18n import columns, t

user = current_user()
uid = user["id"]

opp_count = get_db().query("SELECT COUNT(*) AS n FROM opportunities WHERE workspace = %s AND status = 'ACTIVE'",
                           (current_workspace(),), label="화면: 기회 수")[0]["n"]
if not opp_count:
    st.warning(t("p3.no_opps"))
    st.stop()

if waiting_threads(uid, "order_approval"):
    st.info(t("p3.pending_order"))
    if st.button(t("p3.go_approval"), type="primary", icon=":material/arrow_forward:"):
        st.switch_page(PAGE_APPROVAL)

st.write(t("p3.intro", name=persona_name(user)))
if st.button(t("p3.new"), type="primary", icon=":material/auto_awesome:"):
    start_recommendation(uid)
    st.rerun()

thread_id = latest_thread(uid)
snap = thread_snapshot(thread_id) if thread_id else None
waiting = pending(snap)
if not snap or not snap.values:
    st.stop()

state = snap.values
st.divider()
left, right = st.columns([2, 1])
with right:
    st.markdown(t("p3.used_info"))
    intent, reason = localized([state.get("intent", ""), state.get("intent_reason", "")])
    st.caption(t("p3.intent"))
    st.info(intent)
    st.caption(reason)
    used = state.get("memories", [])
    with st.expander(t("p3.memories", n=len(used))):
        lines = dict(zip((id(m) for m in used), memory_lines(used)))
        for source in MEMORY_SOURCES:
            if group := [m for m in used if memory_source(m) == source]:
                st.caption(t(f"mem.{source}", n=len(group)).replace("**", ""))
                for m in group:
                    st.markdown(f"- {lines[id(m)]}")
        # 이 추천이 실제로 받은 mem9 목록 응답 (GET /v1alpha2/mem9s/memories, 첫 페이지)
        if (raw := state.get("memories_response")) and st.toggle(t("p3.raw_response"), key="raw_mem9"):
            st.caption(t("p3.raw_caption", total=raw.get("total", "-"), n=len(raw.get("memories", []))))
            st.json(raw, expanded=3)
    candidates = state.get("candidates", [])
    with st.expander(t("p3.candidates", n=len(candidates))):
        titles = localized([c["title"] for c in candidates])
        st.dataframe(columns([{"distance": round(c["distance"], 3), "opportunity": title, "risk": c["risk_level"],
                               "apy": c["expected_apy"]} for c, title in zip(candidates, titles)]),
                     hide_index=True, alt=t("p3.candidates", n=len(candidates)))

with left:
    if state.get("error"):
        st.warning(localized([state["error"]])[0])
    elif waiting and waiting.get("type") == "choose_card":
        st.markdown(t("p3.cards_header"))
        for card in localize_cards(waiting["cards"]):
            if render_card(card, button_key=f"exec_{card['recommendation_id']}"):
                request_execution(thread_id, card["recommendation_id"])
                st.switch_page(PAGE_APPROVAL)
    else:
        outcome = state.get("outcome") or "pending"
        if selected := state.get("selected"):
            st.success(t("p3.outcome", title=localized([selected["title"]])[0], label=t(f"p3.outcome.{outcome}")))
        for card in localize_cards(state.get("cards", [])):
            render_card(card)
