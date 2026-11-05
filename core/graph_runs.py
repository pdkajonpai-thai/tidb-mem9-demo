"""그래프 실행 시간 기록: 노드가 끝날 때마다 시각을 남겨 노드별 걸린 시간을 계산한다.

recommend_graph는 노드가 순서대로 실행되므로, 앞 노드가 끝난 때부터 이 노드가 끝난 때까지를
이 노드의 시간으로 본다. Behind the scenes의 LangGraph 탭과 run_scenario.py --timing이 함께 쓴다.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Iterator

from core.call_log import CallLog

runs = CallLog(maxlen=30)      # 작업 공간별 최근 실행 (최신순으로 읽는다)

INTERRUPT = "__interrupt__"


def stream_timed(graph, graph_input, config: dict, *, phase: str,
                 stream_mode: tuple[str, ...] = ("updates",)) -> Iterator[tuple[str, Any]]:
    """graph.stream의 (mode, payload)를 그대로 넘겨주면서 노드별 시간을 잰다. 끝나면 runs에 기록한다.

    phase: recommend(추천) / execute(실행 요청) / decide(승인·거절)
    """
    started_at = datetime.now().strftime("%H:%M:%S")
    start = last = time.perf_counter()
    nodes: list[dict[str, Any]] = []
    try:
        for mode, payload in graph.stream(graph_input, config, stream_mode=list(stream_mode)):
            if mode == "updates":
                now = time.perf_counter()
                for i, node in enumerate(payload):
                    nodes.append({"node": node, "ms": 0 if i or node == INTERRUPT else round((now - last) * 1000)})
                last = now
            yield mode, payload
    finally:
        runs.append({"thread_id": config["configurable"]["thread_id"], "phase": phase, "at": started_at,
                     "total_ms": round((time.perf_counter() - start) * 1000), "nodes": nodes})


def format_run(run: dict) -> str:
    """한 줄 요약: recall_memory 812ms, build_intent 2,140ms, ..."""
    parts = [f"{n['node']} {n['ms']:,}ms" for n in run["nodes"] if n["node"] != INTERRUPT]
    return f"{run['total_ms']:,}ms = " + ", ".join(parts)
