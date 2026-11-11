"""Graph run timing: records the time each node finishes to compute the time taken per node.

recommend_graph runs its nodes in sequence, so a node's time is taken as from when the previous node
finished until this node finished. Used by both the LangGraph tab of Behind the scenes and run_scenario.py --timing.
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Iterator

from core.call_log import CallLog

runs = CallLog(maxlen=30)      # recent runs per workspace (read newest first)

INTERRUPT = "__interrupt__"


def stream_timed(graph, graph_input, config: dict, *, phase: str,
                 stream_mode: tuple[str, ...] = ("updates",)) -> Iterator[tuple[str, Any]]:
    """Passes graph.stream's (mode, payload) through unchanged while timing each node. Records to runs when done.

    phase: recommend (recommendation) / execute (execution request) / decide (approve/reject)
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
    """One-line summary: recall_memory 812ms, build_intent 2,140ms, ..."""
    parts = [f"{n['node']} {n['ms']:,}ms" for n in run["nodes"] if n["node"] != INTERRUPT]
    return f"{run['total_ms']:,}ms = " + ", ".join(parts)
