"""mem9 REST client (httpx), with call logging.

Hosted API behavior confirmed by the smoke test (2026-10-29):
- q search is closer to word matching than semantic search (Korean words with particles attached are not found).
  → For recommendations, the full list of the user's (appId) memories is fetched and passed to the LLM.
- content writes are only allowed with memory_type=pinned (insight returns 400).
- messages + mode=smart is processed asynchronously (202); insights are usually extracted within 10–25 seconds.
  Sometimes nothing is extracted, so callers have a fallback path.
- Extracted insights are stored in English even when the input is Korean.
- The list response is {"memories": [...], "total", "limit", "offset"}. If total exceeds one page, page with offset.
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Any, Callable

import httpx

from core.call_log import CallLog
from core.config import get_settings

MEMORIES = "/v1alpha2/mem9s/memories"
RATE_LIMIT_RETRIES = 3     # retry count for 429 Too Many Requests
PAGE_SIZE = 100
SHOWN_ITEMS = 20           # how many leading memories of a response to keep for the on-screen record


def trimmed(response: Any, n: int = SHOWN_ITEMS) -> Any:
    """Copy of the response for display: if memories is long, keep only the first n and note how many were cut."""
    if not isinstance(response, dict) or len(response.get("memories") or []) <= n:
        return response
    return {**response, "memories": response["memories"][:n], "_truncated": len(response["memories"]) - n}


def user_app_id(user_id: int, workspace: str) -> str:
    """Memory space per workspace × user (ARCHITECTURE.md section 4.1)."""
    return f"{workspace}_user_{user_id}"


class Mem9Client:
    def __init__(self, base_url: str | None = None, api_key: str | None = None, timeout: float = 60):
        s = get_settings()
        self._http = httpx.Client(
            base_url=base_url or s.mem9_api_url,
            headers={"X-API-Key": api_key or s.mem9_api_key},
            timeout=timeout,
        )
        self.calls = CallLog(maxlen=200)   # call log for the Behind the scenes screen (per workspace)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        res = self._send(method, path, **kwargs)
        return res.json() if res.content else None

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            start = time.perf_counter()
            res = self._http.request(method, path, **kwargs)
            try:
                response = res.json() if res.content else None
            except ValueError:
                response = res.text[:2000]
            self.calls.append({
                "method": method,
                "path": path,
                "params": kwargs.get("params"),
                "body": kwargs.get("json"),
                "status": res.status_code,
                "ms": round((time.perf_counter() - start) * 1000),
                "response": trimmed(response),
            })
            # On a rate limit (429), wait for Retry-After (or 1/2/4 seconds if absent) and resend
            if res.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            retry_after = res.headers.get("Retry-After", "")
            time.sleep(min(float(retry_after), 10) if retry_after.isdigit() else 2 ** attempt)
        res.raise_for_status()
        return res

    def status(self) -> str:
        return self._request("GET", "/v1alpha2/status").get("status", "")

    def list_memories(
        self,
        app_id: str,
        *,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[dict]:
        return self.list_page(app_id, memory_type=memory_type, tags=tags, limit=limit).get("memories", [])

    def list_page(
        self,
        app_id: str,
        *,
        memory_type: str | None = None,
        tags: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """The list response as is ({"memories", "total", "limit", "offset"})."""
        params: dict[str, Any] = {"appId": app_id, "limit": limit}
        if offset:
            params["offset"] = offset
        if memory_type:
            params["memory_type"] = memory_type
        if tags:
            params["tags"] = ",".join(tags)
        return self._request("GET", MEMORIES, params=params) or {}

    def list_all(self, app_id: str) -> tuple[list[dict], dict]:
        """Fetches all memories, paging through them. Returns (full list, first page response).

        Keeps memories beyond the per-request limit from being dropped (so recommendations don't miss old ones).
        """
        first = self.list_page(app_id, limit=PAGE_SIZE)
        memories = list(first.get("memories", []))
        while len(memories) < first.get("total", 0):
            page = self.list_page(app_id, limit=PAGE_SIZE, offset=len(memories)).get("memories", [])
            if not page:
                break
            memories += page
        return memories, first

    def add_pinned(
        self, app_id: str, content: str, *, tags: list[str] | None = None, metadata: dict | None = None
    ) -> dict:
        body: dict[str, Any] = {"appId": app_id, "content": content, "memory_type": "pinned", "sync": True}
        if tags:
            body["tags"] = tags
        if metadata:
            body["metadata"] = metadata
        return self._request("POST", MEMORIES, json=body)

    def ingest(self, app_id: str, messages: list[dict[str, str]]) -> int:
        """Sends a conversation so mem9 extracts and reconciles facts (async; check results with wait_for_insights).

        Returns the HTTP status code (202 when accepted).
        """
        return self._send("POST", MEMORIES, json={"appId": app_id, "mode": "smart", "messages": messages}).status_code

    def wait_for_insights(
        self, app_id: str, known_ids: set[str], *, timeout: float = 30, interval: float = 1,
        on_poll: Callable[[int], None] | None = None,
    ) -> list[dict]:
        """Waits until an insight not in known_ids appears. Returns an empty list if none appears in time.

        on_poll(n) is called after each n-th check (for progress display).
        """
        deadline = time.monotonic() + timeout
        polls = 0
        while True:
            new = [m for m in self.list_memories(app_id, memory_type="insight") if m["id"] not in known_ids]
            polls += 1
            if on_poll:
                on_poll(polls)
            if new or time.monotonic() >= deadline:
                return new
            time.sleep(interval)

    def delete(self, memory_id: str) -> None:
        self._request("DELETE", f"{MEMORIES}/{memory_id}")

    def clear(self, app_id: str) -> int:
        """Deletes all memories of the appId (for the demo reset)."""
        memories, _ = self.list_all(app_id)
        for m in memories:
            self.delete(m["id"])
        return len(memories)

    def close(self) -> None:
        self._http.close()


@lru_cache
def get_mem9() -> Mem9Client:
    """Client shared by the whole process (the call log accumulates with it)."""
    return Mem9Client()
