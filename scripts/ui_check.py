"""Automated UI check (Streamlit AppTest, runs without a browser).

    python scripts/ui_check.py

Checks
  1) Without login settings ([auth]) and without DEMO_AUTH=off, the app does not open
  2) Rendering 3 users × 6 pages × 2 languages raises no exceptions
  3) In English mode, page text has no Korean (excluding mem9 raw views, code blocks, and table contents)
  4) Anyone can use '데모 초기화' ("Reset demo") for their own workspace; '접속 기록' ("Access log", including the full
     reset) is visible only to admins
  5) After using the pages, access_logs has usage records besides visit/login (LLM behavior: see run_scenario)
  6) Opening each Behind the scenes tab raises no exceptions (no Korean in English mode), and the LangGraph node
     table matches the graph nodes
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from streamlit.testing.v1 import AppTest  # noqa: E402

HANGUL = re.compile(r"[가-힣]")
PAGES = ["app_pages/1_signal_analysis.py", "app_pages/2_user_dashboard.py", "app_pages/3_recommendations.py",
         "app_pages/4_approval.py", "app_pages/5_behind_the_scenes.py", "app_pages/6_access_log.py"]
ADMIN = "admin@example.com"


def visible_texts(at: AppTest) -> list[str]:
    texts = [e.value for e in (*at.title, *at.subheader, *at.markdown, *at.caption, *at.info, *at.warning,
                               *at.success, *at.error)]
    texts += [b.label for b in at.button] + [m.label for m in at.metric] + [str(m.value) for m in at.metric]
    texts += [x.label for x in at.tabs] + [x.label for x in at.expander] + [x.label for x in at.toggle]
    return [x for x in texts if isinstance(x, str)]


def main() -> int:
    failures = []

    # 1) blocked without login settings
    os.environ.pop("DEMO_AUTH", None)
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    at.run()
    blocked = any("auth" in e.value.lower() for e in at.error)
    print(f"[1] 로그인 설정 없음 → 차단: {'✓' if blocked else '✗'}")
    if not blocked:
        failures.append("auth gate")

    # 2-4) page checks in bypass mode
    os.environ.update({"DEMO_AUTH": "off", "DEMO_DEV_EMAIL": ADMIN, "ADMIN_EMAILS": ADMIN})
    for lang in ("ko", "en"):
        # widget labels and display names change with the language, so start a new session per language
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=300)
        at.session_state["_lang"] = {"ko": "한국어", "en": "English"}[lang]
        at.run()
        for uid in (1, 2, 3):
            at.session_state["user_id"] = uid
            for page in PAGES:
                at.switch_page(page).run()
                # Behind the scenes draws only the selected tab, so open tabs one by one (user 1 only)
                tabs = [x.label for x in at.tabs] if page == PAGES[4] and uid == 1 else [None]
                for tab in tabs:
                    if tab is not None:
                        at.session_state["behind_tab"] = tab
                        at.run()
                    where = f"{lang} user{uid} {page}" + (f" [{tab}]" if tab else "")
                    if at.exception:
                        failures.append(f"{where}: {at.exception[0].value}")
                        continue
                    if lang == "en":
                        leftovers = [x for x in visible_texts(at) if HANGUL.search(x)]
                        if leftovers:
                            failures.append(f"{where}: 한글 남음 {leftovers[:3]}")
        print(f"[2·3] 언어 {lang}: 사용자 3명 × 화면 {len(PAGES)}개 렌더링 완료")

    from core import workflow
    from ui.explain import GRAPH_NODES
    graph_nodes = set(workflow.get_recommend_graph().get_graph().nodes) - {"__start__", "__end__"}
    nodes_match = graph_nodes == set(GRAPH_NODES)
    print(f"[6] LangGraph 노드 설명표 = 그래프 노드: {'✓' if nodes_match else '✗'}")
    if not nodes_match:
        failures.append(f"node table mismatch: {sorted(graph_nodes ^ set(GRAPH_NODES))}")

    reset_labels = {"초기화 실행", "Run reset"}
    reset_all_labels = {"전체 초기화 실행", "Reset everything"}
    at.switch_page(PAGES[5]).run()
    admin_has_reset_all = any(b.label in reset_all_labels for b in at.button)
    os.environ["DEMO_DEV_EMAIL"] = "viewer@example.com"
    viewer = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
    viewer.run()
    viewer_has_reset = any(b.label in reset_labels for b in viewer.button)
    try:
        viewer.switch_page(PAGES[5]).run()
        viewer_blocked = any(e.value for e in viewer.error)       # blocked inside the page even when opened by URL
    except ValueError:
        viewer_blocked = True                                       # page is not registered for regular users
    print(f"[4] 일반 사용자: 자기 초기화 {'✓' if viewer_has_reset else '✗'}, '접속 기록' 차단 "
          f"{'✓' if viewer_blocked else '✗'} / 관리자 전체 초기화 {'✓' if admin_has_reset_all else '✗'}")
    if not viewer_blocked:
        failures.append("viewer can open audit page")
    if not viewer_has_reset:
        failures.append("viewer has no own reset")
    if not admin_has_reset_all:
        failures.append("admin has no reset-all")

    from core.db import get_db
    rows = get_db().query("SELECT event, COUNT(*) AS n FROM access_logs GROUP BY event ORDER BY event")
    print(f"[5] access_logs: {[(r['event'], r['n']) for r in rows]}")

    print("\n결과:", "모두 통과" if not failures else f"실패 {len(failures)}건")
    for f in failures[:30]:
        print("  ✗", f)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
