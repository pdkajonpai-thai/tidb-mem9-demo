"""Entry point: login gate, language toggle, navigation (st.navigation), sidebar (demo user, reset, notice)."""
import threading

import streamlit as st

from core.config import missing

st.set_page_config(page_title="TiDB + mem9 AI demo", page_icon="📈", layout="wide")

from ui.i18n import lang_toggle, t  # noqa: E402

if absent := missing():
    st.error(t("err.env_missing", keys=", ".join(absent)))
    st.stop()

from core import audit, call_log  # noqa: E402  (connect to the DB after checking env vars)
from core.workspace import ensure_workspace, reset_workspace  # noqa: E402
from ui.common import current_user_id, current_workspace, persona_name, users  # noqa: E402


@st.cache_resource
def _reset_locks() -> tuple[threading.Lock, dict[str, threading.Lock]]:
    return threading.Lock(), {}


def reset_lock(workspace: str) -> threading.Lock:
    """Allow only one reset at a time per workspace (runs once even if the same account clicks in two windows)."""
    guard, locks = _reset_locks()
    with guard:
        return locks.setdefault(workspace, threading.Lock())


# ---- Login gate ----
if not audit.auth_bypassed() and not audit.auth_configured():
    st.error(t("err.auth_missing"))     # don't let a config mistake open the app unprotected
    st.stop()

if not audit.is_logged_in():
    lang_toggle()
    st.title(t("app.title"))
    st.write(t("login.intro"))
    st.caption(t("login.notice"))
    audit.log_once("visit")
    if st.button(t("login.button"), type="primary", icon=":material/login:"):
        st.login()
    st.stop()

audit.log_once("login")
is_admin = audit.is_admin()
workspace = current_workspace()
call_log.current_workspace.set(workspace)   # log this run's SQL and mem9 calls to my workspace's records
with st.spinner(t("ws.preparing")):     # prepare mem9 memories only for a first-time workspace (a few seconds)
    ensure_workspace(workspace)

PAGES = [
    st.Page("app_pages/1_signal_analysis.py", title=t("nav.signal"), icon=":material/feed:", default=True),
    st.Page("app_pages/2_user_dashboard.py", title=t("nav.dashboard"), icon=":material/account_balance_wallet:",
            url_path="dashboard"),
    st.Page("app_pages/3_recommendations.py", title=t("nav.recommend"), icon=":material/auto_awesome:",
            url_path="recommendations"),
    st.Page("app_pages/4_approval.py", title=t("nav.approval"), icon=":material/task_alt:", url_path="approval"),
    st.Page("app_pages/5_behind_the_scenes.py", title=t("nav.behind"), icon=":material/manage_search:",
            url_path="behind-the-scenes"),
]
if is_admin:
    PAGES.append(st.Page("app_pages/6_access_log.py", title=t("nav.audit"), icon=":material/admin_panel_settings:",
                         url_path="access-log"))
nav = st.navigation(PAGES)

with st.sidebar:
    st.markdown(f"### {t('app.sidebar_title')}")
    lang_toggle()
    people = users()
    ids = [u["id"] for u in people]
    names = {u["id"]: persona_name(u) for u in people}     # build display names in this run's language up front
    st.session_state["user_id"] = st.selectbox(
        t("side.persona"), ids, index=ids.index(current_user_id()) if current_user_id() in ids else 0,
        format_func=names.get)

    # Run after a reset finishes: close the popover and show a done toast
    # (widget state can only change before the widget is drawn)
    if st.session_state.pop("_reset_done", False):
        st.session_state["reset_popover"] = False
        st.toast(t("side.reset_done"), icon=":material/check_circle:")
    with st.popover(t("side.reset"), icon=":material/restart_alt:", key="reset_popover", on_change="rerun"):
        st.write(t("side.reset_body"))
        # On the rerun after a click, disable the button and reset in that run (prevents double clicks)
        resetting = st.session_state.get("_resetting", False)
        st.button(t("side.reset_run"), type="primary", disabled=resetting,
                  on_click=lambda: st.session_state.update(_resetting=True))
        if resetting:
            lock = reset_lock(workspace)
            if not lock.acquire(blocking=False):   # already resetting in another window of the same account
                st.session_state.pop("_resetting", None)
                st.warning(t("side.reset_busy"))
            else:
                try:
                    audit.log_event("reset", {"workspace": workspace})
                    with st.spinner(t("side.reset_spinner")):
                        reset_workspace(workspace)
                finally:
                    lock.release()
                for key in list(st.session_state):
                    if key not in ("user_id", "_lang", "lang_choice", "reset_popover") and not key.startswith("_audit"):
                        del st.session_state[key]
                st.session_state["_reset_done"] = True
                st.rerun()

    st.divider()
    st.caption(t("side.workspace", ws=workspace))
    who = audit.identity()
    if audit.auth_bypassed():
        st.caption(t("login.dev_mode", email=who["email"]))
    else:
        st.caption(t("side.signed_in", email=who["email"]))
        if st.button(t("side.logout"), icon=":material/logout:"):
            audit.log_event("logout")
            st.logout()
    st.caption(t("disclaimer"))

st.title(nav.title)
st.caption(t("disclaimer"))
nav.run()
