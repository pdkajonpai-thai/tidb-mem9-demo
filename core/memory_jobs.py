"""mem9 기억 정리를 백그라운드에서 돌리는 작업 (승인·거절 직후).

결정과 체결은 바로 끝내고, 이유 ingest → mem9 추출 대기(또는 Claude 대체 추출) → 결정·체결 기록은
뒤에서 진행한다. 화면은 작업의 단계(steps)를 읽어 "mem9 기억 정리" 타임라인으로 보여준다.
다음 추천은 recall_memory에서 wait_pending()으로 정리가 끝나기를 기다린 뒤 기억을 읽는다.

작업은 서버 메모리에만 있다. 앱이 재시작되면 진행 중이던 작업은 사라진다 (데모 범위에서 허용).
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

KEEP_SECONDS = 600          # 끝난 작업을 화면에 보여줄 수 있게 남겨 두는 시간


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
        """단계를 추가한다. 같은 key가 진행 중(running)이면 그 단계를 갱신한다."""
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
    """fn(job)을 백그라운드에서 실행한다. 현재 작업 공간(ContextVar)도 함께 넘겨 호출 기록이 제자리에 남게 한다."""
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
        except Exception as e:      # 기억 정리가 실패해도 결정·체결은 이미 끝났다
            job.error = f"{type(e).__name__}: {e}"
            logger.warning("mem9 기억 작업 실패 (app=%s)", app_id, exc_info=True)
        finally:
            job.finished = time.monotonic()
            job._done.set()

    _executor.submit(run)
    return job.id


def run_later(fn: Callable[..., Any], *args: Any) -> None:
    """기다릴 필요가 없는 뒷정리(미리 번역 등)를 백그라운드에서 실행한다. wait_pending은 이 작업을 기다리지 않는다."""
    ctx = contextvars.copy_context()

    def run() -> None:
        try:
            ctx.run(fn, *args)
        except Exception:
            logger.warning("백그라운드 뒷정리 실패 (%s)", getattr(fn, "__name__", fn), exc_info=True)

    try:
        _executor.submit(run)
    except RuntimeError:        # 프로세스 종료 중(스크립트 끝)에는 뒷정리를 건너뛴다
        pass


def get(job_id: str | None) -> Job | None:
    with _lock:
        return _jobs.get(job_id) if job_id else None


def pending(app_id: str) -> list[Job]:
    with _lock:
        return [j for j in _jobs.values() if j.app_id == app_id and j.finished is None]


def wait_pending(app_id: str, timeout: float = 20) -> None:
    """이 기억 공간에 진행 중인 작업이 있으면 끝날 때까지 기다린다 (다음 추천이 새 기억을 읽도록)."""
    deadline = time.monotonic() + timeout
    for job in pending(app_id):
        job.wait(max(0.0, deadline - time.monotonic()))
