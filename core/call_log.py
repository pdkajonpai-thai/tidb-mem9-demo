"""Call records (SQL, mem9) for the Behind the scenes screen, kept separately per workspace.

The DB connection and the mem9 client are shared by the whole server, so the workspace that made a call
is found through a ContextVar. app.py sets the current workspace at the start of every run, and LangGraph
copies this value over when it runs nodes on other threads. Calls without a workspace (scripts, full reset)
do not appear on anyone's screen.
"""
from __future__ import annotations

import threading
from collections import deque
from contextvars import ContextVar
from typing import Any

current_workspace: ContextVar[str | None] = ContextVar("current_workspace", default=None)


class CallLog:
    def __init__(self, maxlen: int = 200):
        self._maxlen = maxlen
        self._lock = threading.Lock()
        self._entries: dict[str | None, deque[dict[str, Any]]] = {}

    def append(self, entry: dict[str, Any]) -> None:
        workspace = current_workspace.get()
        with self._lock:
            self._entries.setdefault(workspace, deque(maxlen=self._maxlen)).append(entry)

    def recent(self, workspace: str) -> list[dict[str, Any]]:
        """Records for this workspace (newest first)."""
        with self._lock:
            return list(reversed(self._entries.get(workspace, ())))
