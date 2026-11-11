"""⑤ Behind the scenes: SQL Inspector, mem9 calls (request/response), engine calls, LangGraph (graph, node
descriptions, execution path and timing, checkpoint history), technical notes (mem9 memory compression and loss, LangGraph setup),
architecture."""
import json

import streamlit as st

from core import engine_mock, graph_runs, workflow
from core.db import get_db
from core.mem9_client import get_mem9
from core.schemas import ExecutionCommand
from ui.common import current_user_id, current_workspace, latest_thread, localized
from ui import explain
from ui.i18n import ARCHITECTURE, columns, label, lang, t

MERMAID_JS = "https://cdn.jsdelivr.net/npm/mermaid@10.9.1/dist/mermaid.min.js"


def pick(pair: tuple[str, str]) -> str:
    return pair[0] if lang() == "ko" else pair[1]


def mermaid(code: str, alt: str, height: int = 520) -> None:
    # only graph definitions built by code and fixed diagrams go in (no LLM output or user input)
    theme = "dark" if st.context.theme.type == "dark" else "neutral"
    st.iframe(
        f"""<div class="mermaid">{code}</div>
        <script src="{MERMAID_JS}"></script>
        <script>mermaid.initialize({{startOnLoad: true, theme: "{theme}"}});</script>""",
        height=height, alt=alt)


db = get_db()
# Draw only the selected tab. A Mermaid iframe created inside a hidden tab has zero size, so nothing renders
tab_sql, tab_mem9, tab_engine, tab_graph, tab_explain, tab_arch = st.tabs(
    [t("p5.tab_sql"), t("p5.tab_mem9"), t("p5.tab_engine"), t("p5.tab_graph"), t("p5.tab_explain"), t("p5.tab_arch")],
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
        only_reads = st.toggle(t("p5.only_reads"))
        if only_reads:
            calls = [c for c in calls if c["method"] == "GET"]
        if not calls:
            st.info(t("p5.mem9_empty"))
        for c in calls[:60]:
            with st.expander(f"{c['method']} {c['path']} · {c['status']} · {c['ms']}ms",
                             expanded=only_reads and c is calls[0]):
                st.caption(t("p5.mem9_request"))
                st.json({k: c[k] for k in ("params", "body") if c.get(k)} or {}, expanded=False)
                st.caption(t("p5.engine_response"))
                if c.get("response") is None:
                    st.caption(t("p5.no_body"))
                else:
                    st.json(c["response"], expanded=3)

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
        thread_id = latest_thread(current_user_id())
        runs = [r for r in reversed(graph_runs.runs.recent(current_workspace()))
                if thread_id and r["thread_id"] == thread_id]
        visited = list(dict.fromkeys(n["node"] for r in runs for n in r["nodes"] if n["node"] in explain.GRAPH_NODES))

        st.markdown(t("p5.graph_title"))
        # LangGraph's default node color (light purple) is unreadable in dark theme, so use Mermaid theme colors
        # and fill only the nodes that were visited
        graph_code = workflow.get_recommend_graph().get_graph().draw_mermaid(with_styles=False)
        if visited:
            graph_code += f"\t{explain.VISITED_STYLE}\n\tclass {','.join(visited)} visited;\n"
            st.caption(t("p5.visited_caption"))
        mermaid(graph_code, alt=t("p5.graph_alt"), height=1000)

        st.markdown(t("p5.nodes_title"))
        st.dataframe(columns([{"node": name, **{k: pick(v) for k, v in spec.items()}}
                              for name, spec in explain.GRAPH_NODES.items()]),
                     hide_index=True, alt=t("p5.nodes_title"))

        st.markdown(t("p5.timing_title"))
        if not runs:
            st.info(t("p5.timing_empty"))
        else:
            rows = [{"phase": t("phase." + r["phase"]), "node": n["node"], "ms": n["ms"]}
                    for r in runs for n in r["nodes"] if n["node"] != graph_runs.INTERRUPT]
            ms_col = t("col.ms")
            st.dataframe(columns(rows), hide_index=True, alt=t("p5.timing_title"),
                         column_config={ms_col: st.column_config.ProgressColumn(
                             ms_col, format="%d ms", min_value=0, max_value=max(r["ms"] for r in rows) or 1)})
            st.caption(" / ".join(t("p5.timing_total", phase=t("phase." + r["phase"]), s=r["total_ms"] / 1000)
                                  for r in runs))

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

        st.markdown(t("p5.analysis_graph"))
        mermaid(workflow.get_analysis_graph().get_graph().draw_mermaid(with_styles=False),
                alt=t("p5.analysis_graph_alt"), height=560)

if tab_explain.open:
    with tab_explain:
        m = explain.MEM9_TEXT[lang()]
        st.markdown(m["title"])
        st.write(m["intro"])
        mermaid(explain.MEM9_FLOW[lang()], alt=t("p5.mem9_flow_alt"), height=360)
        st.markdown(m["how"])
        st.markdown(m["loss_title"])
        st.table([dict(zip(m["loss_cols"], row)) for row in m["loss"]])
        st.markdown(m["where"])
        st.caption(m["source"])
        st.divider()
        g = explain.LANGGRAPH_TEXT[lang()]
        st.markdown(g["title"])
        st.write(g["intro"])
        mermaid(explain.LANGGRAPH_FLOW[lang()], alt=t("p5.langgraph_flow_alt"), height=680)
        st.table([dict(zip(g["cols"], row)) for row in g["rows"]])
        st.markdown(g["where"])

if tab_arch.open:
    with tab_arch:
        mermaid(ARCHITECTURE[lang()], alt=t("p5.arch_alt"), height=420)
        st.markdown(t("p5.arch_bullets"))
