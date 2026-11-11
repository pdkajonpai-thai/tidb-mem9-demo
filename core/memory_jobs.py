"""Jobs that run mem9 memory updates in the background (right after approve/reject).

The decision and the fill finish immediately; reason ingest → waiting for mem9 extraction (or Claude fallback
extraction) → recording the decision and fill proceed in the background. The screen reads the job's steps and
shows them as the "mem9 기억 정리" ("mem9 memory update") timeline.
The next recommendation waits in recall_memory via wait_pending() for the update to finish, then reads memories.

Jobs live only in server memory. If the app restarts, in-progress jobs are lost (acceptable for the demo).
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

logger = logging.getLogger(__name__)

KEEP_SECONDS = 600          # how long finished jobs are kept so the screen can still show them


class Job:
    def __init__(self, app_id: str):
        self.id = uuid.uuid4().hex[:12]
        self.app_id = app_id
        self.started = time.monotonic()
        self.finished: float | None = None
        self.steps: list[dict[str, Any]] = []     # {key, at, state(running/done/warn), detail}
        self.events: list[dict[str, Any]] = []    # decision / insight / fallback / execution
        self.error: str | None = None
        self._lock = threading.Lock()
        self._done = threading.Event()

    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started

    def step(self, key: str, state: str = "done", **detail: Any) -> None:
        """Adds a step. If a step with the same key is in progress (running), updates that step instead."""
        with self._lock:
            last = self.steps[-1] if self.steps else None
            if last and last["key"] == key and last["state"] == "running":
                last.update(state=state, detail={**last["detail"], **detail})
            else:
                self.steps.append({"key": key, "at": round(self.elapsed(), 1), "state": state, "detail": detail})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"id": self.id, "running": self.finished is None, "elapsed": round(self.elapsed(), 1),
                    "steps": [dict(s, detail=dict(s["detail"])) for s in self.steps],
                    "events": list(self.events), "error": self.error}

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)


_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="mem9-job")
_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def submit(app_id: str, fn: Callable[[Job], None]) -> str:
    """Runs fn(job) in the background, passing the current workspace (ContextVar) so call records land correctly."""
    job = Job(app_id)
    with _lock:
        now = time.monotonic()
        for jid in [j.id for j in _jobs.values() if j.finished and now - j.finished > KEEP_SECONDS]:
            del _jobs[jid]
        _jobs[job.id] = job
    ctx = contextvars.copy_context()

    def run() -> None:
        try:
            ctx.run(fn, job)
        except Exception as e:      # even if the memory update fails, the decision and fill are already done
            job.error = f"{type(e).__name__}: {e}"
            logger.warning("mem9 기억 작업 실패 (app=%s)", app_id, exc_info=True)
        finally:
            job.finished = time.monotonic()
            job._done.set()

    _executor.submit(run)
    return job.id


def run_later(fn: Callable[..., Any], *args: Any) -> None:
    """Runs follow-up work that need not be awaited (pre-translation etc.) in the background.
    wait_pending does not wait for it."""
    ctx = contextvars.copy_context()

    def run() -> None:
        try:
            ctx.run(fn, *args)
        except Exception:
            logger.warning("백그라운드 뒷정리 실패 (%s)", getattr(fn, "__name__", fn), exc_info=True)

    try:
        _executor.submit(run)
    except RuntimeError:        # skip follow-up work while the process is shutting down (end of a script)
        pass


def get(job_id: str | None) -> Job | None:
    with _lock:
        return _jobs.get(job_id) if job_id else None


def pending(app_id: str) -> list[Job]:
    with _lock:
        return [j for j in _jobs.values() if j.app_id == app_id and j.finished is None]


def wait_pending(app_id: str, timeout: float = 20) -> None:
    """If this memory space has a job in progress, waits for it to finish (so the next recommendation sees new memories)."""
    deadline = time.monotonic() + timeout
    for job in pending(app_id):
        job.wait(max(0.0, deadline - time.monotonic()))
