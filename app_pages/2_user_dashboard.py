"""② 사용자 대시보드: TiDB의 보유 자산·거래소 계정·해외 거래소 월말 잔고와 mem9 장기 기억."""
import altair as alt
import streamlit as st

from core import memory_jobs
from core.db import get_db
from core.mem9_client import get_mem9, user_app_id
from core.overseas import overseas_status
from ui.common import (MEMORY_SOURCES, current_user, current_workspace, memory_lines, memory_source,
                       memory_updating_note, persona_name)
from ui.i18n import columns, krw, t

db = get_db()
user = current_user()
uid = user["id"]

st.subheader(persona_name(user))
holdings = db.query(
    """SELECT h.asset_symbol AS asset, h.chain AS location, COALESCE(e.region, 'ONCHAIN') AS kind,
              h.amount, h.usd_value
       FROM user_holdings h LEFT JOIN exchanges e ON LOWER(e.name) = h.chain
       WHERE h.user_id = %s ORDER BY h.usd_value DESC""",
    (uid,), label="대시보드: 보유 자산")
with st.container(horizontal=True):
    st.metric(t("p2.risk_profile"), t("risk." + user["risk_profile"]), border=True)
    st.metric(t("p2.risk_limit"), f"{user['max_risk_level']} / 5", border=True)
    st.metric(t("p2.total"), f"${sum(float(h['usd_value']) for h in holdings):,.0f}", border=True)

left, right = st.columns([3, 2])
with left:
    st.markdown(t("p2.holdings"))
    st.dataframe(columns([{**h, "kind": t("region." + h["kind"])} for h in holdings]),
                 hide_index=True, alt=t("p2.holdings"))
    accounts = db.query(
        """
        SELECT e.name AS exchange, e.type, e.region AS kind,
               GROUP_CONCAT(ec.chain ORDER BY ec.chain SEPARATOR ', ') AS reach
        FROM user_exchange_accounts ua
        JOIN exchanges e        ON e.id = ua.exchange_id
        JOIN exchange_chains ec ON ec.exchange_id = e.id
        WHERE ua.user_id = %s AND ua.status = 'ACTIVE'
        GROUP BY e.id, e.name, e.type, e.region
        """, (uid,), label="대시보드: 거래소 계정")
    st.markdown(t("p2.accounts"))
    st.dataframe(columns([{**a, "kind": t("region_short." + a["kind"])} for a in accounts]),
                 hide_index=True, alt=t("p2.accounts"))

# Agent 기억 영역만 배경색을 달리해 TiDB 표와 구분한다 (컨테이너에 배경색 옵션이 없어 이 키에만 CSS를 준다).
# 반투명 색이라 밝은·어두운 테마 모두에서 글자 대비가 유지된다
st.html("""<style>
.st-key-agent_memory { background: rgba(124, 92, 255, 0.08); border-color: rgba(124, 92, 255, 0.35) !important; }
</style>""")
with right, st.container(border=True, key="agent_memory"):
    app_id = user_app_id(uid, current_workspace())
    memories, _ = get_mem9().list_all(app_id)       # 추천과 같이 전체를 읽는다 (한 페이지에서 잘리지 않게)
    shown = [m for m in memories if m["memory_type"] != "session"]
    st.markdown(t("p2.memories", n=len(shown), app=app_id))
    lines = dict(zip((id(m) for m in shown), memory_lines(shown)))     # 번역은 한 번에
    for source in MEMORY_SOURCES:
        group = [m for m in shown if memory_source(m) == source]
        if source == "history" and not group:
            continue
        st.markdown(t(f"mem.{source}", n=len(group)))
        if source == "learned" and memory_jobs.pending(app_id):
            memory_updating_note(app_id)     # 뒤에서 정리 중이면 표시하고, 끝나면 화면을 다시 그린다
        if source != "history":
            st.caption(t(f"mem.{source}_desc"))
        if source == "learned" and not group:
            st.info(t("mem.learned_empty"), icon=":material/lightbulb:")
        for m in group:
            st.markdown(lines[id(m)])
            st.caption(f"{m.get('relative_age', '')} " + " ".join(f"`{x}`" for x in m.get("tags") or []))
    st.caption(t("p2.memory_legend"))
    with st.expander(t("p2.raw_memories")):
        for m in memories:
            st.text(f"[{m['memory_type']}] {m['content']}")

# ---- 해외 거래소 월말 잔고 (해외 계좌 신고 참고 정보) ----
st.divider()
st.markdown(t("p2.overseas_title"))
status = overseas_status(db, uid)
if not status:
    st.caption(t("p2.no_overseas"))
else:
    with st.container(horizontal=True):
        st.metric(t("p2.latest", m=status["latest_month"]), krw(status["latest_krw"]), border=True)
        st.metric(t("p2.peak", m=status["peak_month"]), krw(status["peak_krw"]), border=True)
        st.metric(t("p2.gap"), t("p2.exceeded_value") if status["exceeded"] else krw(status["gap_krw"]), border=True)
    if status["exceeded"]:
        st.warning(t("overseas.notice"), title=t("p2.exceeded_title"))
    elif status["gap_krw"] < 0.2 * status["threshold_krw"]:
        st.info(t("overseas.notice"), title=t("p2.near_title", gap=krw(status["gap_krw"])))

    y_title = t("p2.chart_y")
    data = [{"month": m["month_end"][:7], "value": round(m["total_krw"] / 1e8, 2)} for m in status["months"]]
    bars = alt.Chart(alt.Data(values=data)).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4, size=28).encode(
        x=alt.X("month:O", title=None, axis=alt.Axis(labelAngle=0)),
        y=alt.Y("value:Q", title=y_title, scale=alt.Scale(domain=[0, 6])),
        tooltip=[alt.Tooltip("month:O", title=t("col.at")), alt.Tooltip("value:Q", title=y_title, format=".2f")],
    )
    rule = alt.Chart(alt.Data(values=[{"y": 5}])).mark_rule(strokeDash=[4, 4], color="#8a8f98").encode(y="y:Q")
    rule_label = alt.Chart(alt.Data(values=[{"y": 5, "t": t("p2.chart_rule")}])).mark_text(
        align="left", dx=4, dy=-6, color="#8a8f98").encode(y="y:Q", x=alt.value(0), text="t:N")
    st.altair_chart(bars + rule + rule_label, alt=t("p2.chart_alt"))
    with st.expander(t("p2.by_exchange")):
        st.dataframe(columns([{"exchange": r["exchange"], "asset": r["asset_symbol"], "amount": r["amount"],
                               "krw": krw(r["krw_value"])} for r in status["by_exchange"]]),
                     hide_index=True, alt=t("p2.by_exchange"))
    st.caption(f"{t('overseas.notice')} {t('p2.synthetic')}")
