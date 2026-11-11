"""⑥ Access log (admin only): aggregates login and LLM usage records from TiDB access_logs."""
import csv
import io

import streamlit as st

from core import audit
from core.db import get_db
from ui.i18n import columns, t

if not audit.is_admin():          # hidden from the menu, but also blocked when opened directly by URL
    st.error(t("p6.not_admin"))
    st.stop()

db = get_db()
summary = db.query(
    """
    SELECT COUNT(DISTINCT email) AS users,
           SUM(event = 'login') AS logins,
           SUM(event LIKE 'llm:%%') AS llm,
           COUNT(DISTINCT CASE WHEN DATE(at) = CURDATE() THEN email END) AS users_today,
           SUM(event = 'login' AND DATE(at) = CURDATE()) AS logins_today,
           SUM(event LIKE 'llm:%%' AND DATE(at) = CURDATE()) AS llm_today
    FROM access_logs
    """, label="기록: 요약 지표")[0]
with st.container(horizontal=True):
    st.metric(t("p6.users"), int(summary["users"] or 0), t("p6.today", n=int(summary["users_today"] or 0)),
              delta_color="off", border=True)
    st.metric(t("p6.logins"), int(summary["logins"] or 0), t("p6.today", n=int(summary["logins_today"] or 0)),
              delta_color="off", border=True)
    st.metric(t("p6.llm"), int(summary["llm"] or 0), t("p6.today", n=int(summary["llm_today"] or 0)),
              delta_color="off", border=True)

kinds = [r["event"] for r in db.query("SELECT DISTINCT event FROM access_logs ORDER BY event",
                                      label="기록: 이벤트 조회")]
picked = st.pills(t("p6.filter"), kinds, selection_mode="multi", default=kinds)

where, params = "", ()
if picked:
    where = f"WHERE event IN ({', '.join(['%s'] * len(picked))})"
    params = tuple(picked)
events = db.query(
    f"""SELECT at, event, email, name, detail FROM access_logs {where}
        ORDER BY at DESC LIMIT 500""", params, label="기록: 이벤트 조회") if picked else []

st.markdown(t("p6.recent"))
st.dataframe(columns(events), hide_index=True, alt=t("p6.recent_alt"))
if events:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(events[0]))
    writer.writeheader()
    writer.writerows(events)
    st.download_button(t("p6.download"), buf.getvalue().encode("utf-8-sig"), file_name="access_logs.csv",
                       mime="text/csv", icon=":material/download:")

per_user = db.query(
    """
    SELECT email, MAX(name) AS name, MAX(at) AS last_seen,
           SUM(event = 'login') AS logins, SUM(event LIKE 'llm:%%') AS llm_actions
    FROM access_logs WHERE email IS NOT NULL
    GROUP BY email ORDER BY last_seen DESC
    """, label="기록: 사용자별 집계")
st.markdown(t("p6.per_user"))
st.dataframe(columns(per_user), hide_index=True, alt=t("p6.per_user_alt"))
st.caption(t("p6.caption"))

# ---- Reset everything (all workspaces) ----
st.divider()
st.markdown(t("p6.reset_all_title"))
workspaces = db.query("SELECT COUNT(*) AS n FROM workspaces", label="기록: 작업 공간 수")[0]["n"]
st.write(t("p6.reset_all_body", n=workspaces))
resetting = st.session_state.get("_resetting_all", False)
st.button(t("p6.reset_all_run"), icon=":material/restart_alt:", disabled=resetting,
          on_click=lambda: st.session_state.update(_resetting_all=True))
if resetting:
    from scripts.seed import reset_demo

    audit.log_event("admin:reset_all", {"workspaces": workspaces})
    try:
        with st.spinner(t("side.reset_spinner_all")):
            reset_demo()
    finally:
        st.session_state.pop("_resetting_all", None)
    st.cache_data.clear()
    st.success(t("side.reset_done"))
    st.rerun()
