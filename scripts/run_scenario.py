"""Runs the highlight scene (ARCHITECTURE.md 4.4) end to end without the UI and checks it.

    python scripts/run_scenario.py                  # reset → analysis → round 1 → reject → round 2 → approve/execute

Runs in a scenario-only workspace (scenario@localhost), so other visitors' data is not affected.
    python scripts/run_scenario.py --skip-reset     # start from round 1 with current data and memory as-is
    python scripts/run_scenario.py --user 2
    python scripts/run_scenario.py --lang en       # generate in English (rejection reason in English too)
    python scripts/run_scenario.py --timing        # also print per-node timing for each step

Flow: recommend → choose recommendation (interrupt) → execution agent calls the execute_order tool
      → order approval wait inside the tool (interrupt) → reject or approve

Pass criteria
  1) Round 1 recommendations include an LP
  2) The execution agent calls the execute_order tool and enters approval wait
  3) The rejection reason is kept as a mem9 memory (insight or fallback path)
  4) Round 2 recommendations exclude the rejected opportunity and volatile LPs (risk >= 3) (stable LPs allowed)
  5) Round 2 rationale cites memory (memory_refs is not empty)
  6) After approval the order becomes CONFIRMED
  7) The command shown on the approval page matches the executed and recorded command (unchanged on tool re-run)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from langgraph.types import Command  # noqa: E402

from core import graph_runs, memory_jobs, workflow  # noqa: E402
from core.call_log import current_workspace  # noqa: E402
from core.db import get_db  # noqa: E402
from core.workspace import ensure_workspace, reset_workspace, workspace_id  # noqa: E402

WORKSPACE = workspace_id("scenario@localhost")

APPROVE_REASON = {
    "ko": "언제든 인출할 수 있는 안정적인 이자 수익이 좋아요.",
    "en": "I like steady interest that I can withdraw anytime.",
}
REJECT_REASON = {
    "ko": "변동성이 큰 상품은 부담스러워요. 비영구적 손실도 걱정돼요.",
    "en": "Volatile products make me uneasy. I am also worried about impermanent loss.",
}


TIMING = False
TIMINGS: list[tuple[str, dict]] = []     # (step name, run record): printed as a table at the end


def pending(state: dict) -> dict | None:
    """Returns the interrupt value if the graph is paused."""
    return state["__interrupt__"][0].value if state.get("__interrupt__") else None


def run(graph_input, thread_id: str, phase: str, label: str) -> dict:
    """Runs the same way as the UI (stream) and times each node. Returns the final state like invoke."""
    graph = workflow.get_recommend_graph()
    config = workflow.thread_config(thread_id)
    for _ in graph_runs.stream_timed(graph, graph_input, config, phase=phase):
        pass
    snap = graph.get_state(config)
    state = dict(snap.values)
    if snap.interrupts:
        state["__interrupt__"] = snap.interrupts
    entry = graph_runs.runs.recent(WORKSPACE)[0]
    TIMINGS.append((label, entry))
    if TIMING:
        print(f"  [시간] {graph_runs.format_run(entry)}")
    return state


def recommend(user_id: int, lang: str, label: str) -> tuple[str, dict]:
    thread_id = workflow.new_thread_id(WORKSPACE)
    start = time.perf_counter()
    state = run({"workspace": WORKSPACE, "user_id": user_id, "thread_id": thread_id, "lang": lang},
                thread_id, "recommend", label)
    print(f"  ({time.perf_counter() - start:.1f}초, thread={thread_id})")
    print(f"  기억 {len(state['memories'])}개: " + " / ".join(m["content"] for m in state["memories"]))
    print(f"  검색 의도: {state['intent']}")
    if state.get("error"):
        print(f"  ✗ {state['error']}")
        return thread_id, state
    for c in state["cards"]:
        refs = " / ".join(m["content"] for m in c["memory_refs"]) or "-"
        print(f"  #{c['rank']} {c['headline']}  [{c['action_type']}, 리스크 {c['risk_level']}]")
        print(f"     {c['rationale']}")
        print(f"     참고한 기억: {refs}")
    return thread_id, state


def request_execution(thread_id: str, card: dict, label: str) -> dict | None:
    """On choosing a recommendation, the execution agent calls the tool and pauses at the approval wait inside it."""
    start = time.perf_counter()
    state = run(Command(resume={"recommendation_id": card["recommendation_id"]}), thread_id, "execute", label)
    print(f"  ({time.perf_counter() - start:.1f}초)")
    calls = [tc for m in state.get("messages", []) for tc in getattr(m, "tool_calls", None) or []]
    for tc in calls:
        print(f"  agent → tool {tc['name']}(note={tc['args'].get('note')!r}, 단계 {len(tc['args'].get('steps', []))}개)")
    approval = pending(state)
    if not approval or approval.get("type") != "order_approval":
        print(f"  ✗ 승인 대기에 들어가지 않았습니다: {state.get('outcome')} {state['messages'][-1].content}")
        return None
    sim = approval["simulation"]
    print(f"  승인 화면: {approval['note']}")
    for s in approval["command"]["steps"]:
        print(f"     {s['action']} {s['amount']:g} {s['from_asset']}→{s.get('to_asset') or '-'} @ {s['exchange']} "
              f"({s['chain']}, 슬리피지 {s['slippage_bps']}bps)")
    print(f"     시뮬레이션: 포지션 ${sim['position_usd']}, 가스 ${sim['gas_usd']}, 연 예상 ${sim['expected_yearly_usd']}")
    return approval


def decide(thread_id: str, decision: dict, label: str) -> dict:
    start = time.perf_counter()
    state = run(Command(resume=decision), thread_id, "decide", label)
    print(f"  결정 처리 {time.perf_counter() - start:.1f}초 (화면이 기다리는 시간)")
    print(f"  agent: {state['messages'][-1].content}")
    job = memory_jobs.get(state.get("memory_job"))
    job.wait(60)                      # memory update runs in the background: wait here only to judge the result
    snap = job.snapshot()
    print(f"  mem9 기억 정리 {snap['elapsed']:.1f}초 (백그라운드)" + (f" ✗ {snap['error']}" if snap["error"] else ""))
    for e in snap["events"]:
        print(f"  mem9 {e['kind']}: {e['content']}")
    return {**state, "memory_events": snap["events"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--user", type=int, default=1)
    parser.add_argument("--skip-reset", action="store_true")
    parser.add_argument("--lang", choices=["ko", "en"], default="ko")
    parser.add_argument("--timing", action="store_true", help="노드별 걸린 시간 출력")
    args = parser.parse_args()
    global TIMING
    TIMING = args.timing
    checks: dict[str, bool] = {}

    ensure_workspace(WORKSPACE)
    current_workspace.set(WORKSPACE)     # record calls and timing in the scenario workspace
    if not args.skip_reset:
        print("[0] 데모 초기화")
        t = time.perf_counter()
        reset_workspace(WORKSPACE)
        print(f"  ({time.perf_counter() - t:.1f}초)")
        print("[1] 원문 분석 (analysis_graph)")
        t = time.perf_counter()
        result = workflow.run_analysis(WORKSPACE)
        print(f"  ({time.perf_counter() - t:.1f}초) 원문 {len(result.get('signals', []))}건 → 기회 {result.get('saved', 0)}개, "
              f"제외 {len(result.get('skipped', []))}건")

    print(f"\n[2] 1회차 추천 (사용자 {args.user}, 언어 {args.lang})")
    thread1, s1 = recommend(args.user, args.lang, "1회차 추천")
    if s1.get("error"):
        return 1
    lp = next((c for c in s1["cards"] if c["action_type"] == "lp"), None)
    checks["1회차에 LP 추천"] = lp is not None
    target = lp or s1["cards"][0]

    print(f"\n[3] 실행 요청: '{target['title']}' → 실행 agent")
    approval = request_execution(thread1, target, "1회차 실행 요청")
    checks["agent가 execute_order 호출 → 승인 대기"] = approval is not None
    if approval is None:
        return 1

    print(f"\n[4] 주문 거절 / 사유: {REJECT_REASON[args.lang]}")
    s1 = decide(thread1, {"action": "reject", "reason": REJECT_REASON[args.lang]}, "거절")
    checks["거절 사유가 기억으로 남음"] = any(e["kind"] in ("insight", "fallback") for e in s1.get("memory_events", []))

    print("\n[5] 2회차 추천 (새 스레드)")
    thread2, s2 = recommend(args.user, args.lang, "2회차 추천")
    if s2.get("error"):
        return 1
    checks["2회차에 거절한 기회·변동성 LP 없음"] = all(
        c["opportunity_id"] != target["opportunity_id"]
        and not (c["action_type"] == "lp" and c["risk_level"] >= 3)
        for c in s2["cards"])
    checks["2회차 근거에 기억 인용"] = any(c["memory_refs"] for c in s2["cards"])

    top = s2["cards"][0]
    print(f"\n[6] 실행 요청: '{top['title']}' → 승인")
    approval = request_execution(thread2, top, "2회차 실행 요청")
    if approval is None:
        return 1
    s2 = decide(thread2, {"action": "approve", "reason": APPROVE_REASON[args.lang]}, "승인")
    learned = [e for e in s2.get("memory_events", []) if e["kind"] in ("insight", "fallback")]
    print(f"  (참고) 승인 이유에서 배운 기억 {len(learned)}개")
    order = s2.get("order", {})
    print(f"  주문 {order.get('order_id')} → {order.get('status')} tx={str(order.get('tx_hash'))[:18]}…")
    checks["승인 후 CONFIRMED"] = order.get("status") == "CONFIRMED"

    executed = get_db().query("SELECT command FROM execution_orders WHERE id = %s", (order.get("order_id"),))
    executed_cmd = json.loads(executed[0]["command"]) if executed else {}
    checks["승인 화면 명령 = 실행 명령"] = (
        approval["command"]["command_id"] == s2["command"]["command_id"] == executed_cmd.get("command_id")
        and approval["command"]["steps"] == executed_cmd.get("steps"))

    if TIMING:
        print("\n단계별 시간 (그래프 실행, 화면이 기다리는 시간)")
        for label, entry in TIMINGS:
            print(f"  {label:<12} {entry['total_ms'] / 1000:5.1f}초  {graph_runs.format_run(entry)}")

    print("\n합격 기준")
    for name, ok in checks.items():
        print(f"  {'✓' if ok else '✗'} {name}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
