"""진입점: 로그인 게이트, 언어 토글, 화면 이동(st.navigation), 사이드바(데모 사용자·초기화·고지)."""
import threading

import streamlit as st

from core.config import missing

st.set_page_config(page_title="TiDB + mem9 AI demo", page_icon="📈", layout="wide")

from ui.i18n import lang_toggle, t  # noqa: E402

if absent := missing():
    st.error(t("err.env_missing", keys=", ".join(absent)))
    st.stop()

from core import audit, call_log  # noqa: E402  (환경변수 확인 뒤 DB 연결)
from core.workspace import ensure_workspace, reset_workspace  # noqa: E402
from ui.common import current_user_id, current_workspace, persona_name, users  # noqa: E402


@st.cache_resource
def _reset_locks() -> tuple[threading.Lock, dict[str, threading.Lock]]:
    return threading.Lock(), {}


def reset_lock(workspace: str) -> threading.Lock:
    """작업 공간마다 초기화가 한 번에 하나만 돌게 한다 (같은 계정으로 두 창에서 눌러도 한 번만)."""
    guard, locks = _reset_locks()
    with guard:
        return locks.setdefault(workspace, threading.Lock())


# ---- 로그인 게이트 ----
if not audit.auth_bypassed() and not audit.auth_configured():
    st.error(t("err.auth_missing"))     # 설정 실수로 보호 없이 열리지 않게 한다
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
call_log.current_workspace.set(workspace)   # 이 실행의 SQL·mem9 호출을 내 작업 공간 기록에 남긴다
with st.spinner(t("ws.preparing")):     # 처음 쓰는 작업 공간만 mem9 기억을 준비한다 (몇 초)
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
    names = {u["id"]: persona_name(u) for u in people}     # 표시 이름은 이번 실행의 언어로 미리 만든다
    st.session_state["user_id"] = st.selectbox(
        t("side.persona"), ids, index=ids.index(current_user_id()) if current_user_id() in ids else 0,
        format_func=names.get)

    # 초기화가 끝난 다음 실행: 팝오버를 닫고 완료 알림을 띄운다 (위젯 상태는 위젯을 그리기 전에만 바꿀 수 있다)
    if st.session_state.pop("_reset_done", False):
        st.session_state["reset_popover"] = False
        st.toast(t("side.reset_done"), icon=":material/check_circle:")
    with st.popover(t("side.reset"), icon=":material/restart_alt:", key="reset_popover", on_change="rerun"):
        st.write(t("side.reset_body"))
        # 누른 뒤 다시 그릴 때 버튼을 비활성화하고 그 실행에서 초기화한다 (중복 클릭 방지)
        resetting = st.session_state.get("_resetting", False)
        st.button(t("side.reset_run"), type="primary", disabled=resetting,
                  on_click=lambda: st.session_state.update(_resetting=True))
        if resetting:
            lock = reset_lock(workspace)
            if not lock.acquire(blocking=False):   # 같은 계정의 다른 창에서 이미 초기화 중
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
