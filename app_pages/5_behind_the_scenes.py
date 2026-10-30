"""⑤ Behind the scenes: SQL Inspector, mem9 호출 로그, 엔진 호출 로그, LangGraph 그래프·체크포인트 이력, 아키텍처."""
import json

import streamlit as st

from core import engine_mock, workflow
from core.db import get_db
from core.mem9_client import get_mem9
from core.schemas import ExecutionCommand
from ui.common import current_user_id, current_workspace, latest_thread, localized
from ui.i18n import ARCHITECTURE, label, lang, t

MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


def mermaid(code: str, alt: str, height: int = 520) -> None:
    # 코드가 만든 그래프 정의와 고정 그림만 넣는다 (LLM 출력·사용자 입력은 넣지 않음)
    theme = "dark" if st.context.theme.type == "dark" else "neutral"
    st.iframe(
        f"""<div class="mermaid">{code}</div>
        <script src="{MERMAID_JS}"></script>
        <script>mermaid.initialize({{startOnLoad: true, theme: "{theme}"}});</script>""",
        height=height, alt=alt)


db = get_db()
# 선택한 탭만 그린다. 숨은 탭 안에서 만든 Mermaid iframe은 크기가 0이라 그림이 그려지지 않는다
tab_sql, tab_mem9, tab_engine, tab_graph, tab_arch = st.tabs(
    [t("p5.tab_sql"), t("p5.tab_mem9"), t("p5.tab_engine"), t("p5.tab_graph"), t("p5.tab_arch")],
    key="behind_tab", on_change="rerun")

if tab_sql.open:
    with tab_sql:
        st.caption(t("p5.sql_caption"))
        only_hybrid = st.toggle(t("p5.only_hybrid"))
        entries = [e for e in db.log.recent(current_workspace()) if not only_hybrid or "하이브리드" in e["label"]]
        for e in entries[:60]:
            rows = t("p5.rows", n=e.get("rows", "-"))
            title = f"{e['at']} · {label(e['label']) or 'SQL'} · {e['ms']}ms · {rows}"
            with st.expander(("❌ " if e.get("error") else "") + title, expanded=only_hybrid and e is entries[0]):
                st.code(e["sql"], language="sql")
                if e.get("params") is not None:
                    st.caption(f"params: {e['params']}")
                if e.get("error"):
                    st.error(e["error"])

if tab_mem9.open:
    with tab_mem9:
        calls = get_mem9().calls.recent(current_workspace())
        st.caption(t("p5.mem9_caption", n=len(calls)))
        if not calls:
            st.info(t("p5.mem9_empty"))
        for c in calls[:60]:
            with st.expander(f"{c['method']} {c['path']} · {c['status']} · {c['ms']}ms"):
                if c.get("params"):
                    st.json(c["params"])
                if c.get("body"):
                    st.json(c["body"])

if tab_engine.open:
    with tab_engine:
        calls = engine_mock.calls.recent(current_workspace())
        st.caption(t("p5.engine_caption", n=len(calls)))
        st.markdown(t("p5.engine_contract"))
        st.json(ExecutionCommand.model_json_schema(), expanded=False)
        if not calls:
            st.info(t("p5.engine_empty"))
        for c in calls[:60]:
            with st.expander(f"{c['method']} {c['path']} · {c['status']} · {c['ms']}ms"):
                st.caption(t("p5.engine_request"))
                st.json(c["body"])
                st.caption(t("p5.engine_response"))
                st.json(c["response"])

if tab_graph.open:
    with tab_graph:
        st.markdown(t("p5.graph_title"))
        # LangGraph 기본 노드 색(밝은 보라)은 어두운 테마에서 글자가 보이지 않아 Mermaid 테마 색을 쓴다
        graph_code = workflow.get_recommend_graph().get_graph().draw_mermaid(with_styles=False)
        mermaid(graph_code, alt=t("p5.graph_alt"), height=760)

        thread_id = latest_thread(current_user_id())
        if thread_id:
            st.markdown(t("p5.checkpoints", tid=thread_id))
            rows = db.query(
                """SELECT step, source, checkpoint_id, created_at FROM lg_checkpoints
                   WHERE thread_id = %s ORDER BY checkpoint_id""",
                (thread_id,), label="화면: 체크포인트 이력")
            st.dataframe(rows, hide_index=True, alt=t("p5.checkpoints_alt"))
            values = workflow.get_recommend_graph().get_state(workflow.thread_config(thread_id)).values
            with st.expander(t("p5.state")):
                intent, reason = localized([values.get("intent", ""), values.get("intent_reason", "")])
                st.json({"lang": values.get("lang"), "intent": intent, "intent_reason": reason,
                         "outcome": values.get("outcome"), "exec_tool_calls": values.get("exec_tool_calls")})
                st.code(json.dumps(values.get("command"), ensure_ascii=False, indent=2)
                        if values.get("command") else "-", language="json")

if tab_arch.open:
    with tab_arch:
        mermaid(ARCHITECTURE[lang()], alt=t("p5.arch_alt"), height=420)
        st.markdown(t("p5.arch_bullets"))
