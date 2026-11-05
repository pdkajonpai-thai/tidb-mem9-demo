"""TiDB 연결 (PyMySQL, TLS), SQL 로그 기록.

TiDB Cloud Starter는 TLS가 필수이며, CA는 certifi 번들을 사용한다.
새 TLS 연결은 약 1.3초가 걸리므로(Tokyo 리전 측정) 앱 전체에서 연결 하나를
Lock으로 보호해 재사용한다. 연결 오류가 나면 새로 연결해 한 번 재시도한다.

ORM은 쓰지 않는다. 직접 쓴 SQL을 SQL Inspector에 그대로 보여주는 것이 데모의 목적이다.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import lru_cache
from typing import Any, TypeVar

import certifi
import pymysql

from core.call_log import CallLog

from core.config import get_settings

T = TypeVar("T")
CONNECTION_ERRORS = (pymysql.err.OperationalError, pymysql.err.InterfaceError)


def connect(database: str | None = None, **kwargs) -> pymysql.connections.Connection:
    s = get_settings()
    return pymysql.connect(
        host=s.tidb_host,
        port=s.tidb_port,
        user=s.tidb_user,
        password=s.tidb_password,
        database=database or s.tidb_database,
        ssl_ca=certifi.where(),
        ssl_verify_cert=True,
        ssl_verify_identity=True,
        charset="utf8mb4",
        **kwargs,
    )


class Database:
    def __init__(self, connect_fn: Callable[[], pymysql.connections.Connection] | None = None):
        self._connect = connect_fn or (lambda: connect(autocommit=True))
        self._conn: pymysql.connections.Connection | None = None
        self._lock = threading.RLock()
        self.log = CallLog(maxlen=200)   # SQL Inspector용 (작업 공간별)

    def run(self, fn: Callable[[Any], T], *, transaction: bool = False) -> T:
        """커서를 받아 작업하는 함수를 실행한다. 로그는 남기지 않는다 (체크포인터 내부 SQL 등)."""
        with self._lock:
            for attempt in (1, 2):
                if self._conn is None:
                    self._conn = self._connect()
                try:
                    if transaction:
                        self._conn.begin()
                    with self._conn.cursor() as cur:
                        result = fn(cur)
                    if transaction:
                        self._conn.commit()
                    return result
                except CONNECTION_ERRORS:
                    # 유휴 타임아웃 등으로 끊긴 연결: 버리고 한 번만 새로 연결해 재시도
                    self._drop_conn()
                    if attempt == 2:
                        raise
                except Exception:
                    if transaction:
                        self._conn.rollback()
                    raise
        raise AssertionError("unreachable")

    def _drop_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def close(self) -> None:
        with self._lock:
            self._drop_conn()

    def _logged(
        self, label: str | None, sql: str, params: Any, fn: Callable[[Any], tuple[T, int]]
    ) -> T:
        """fn은 (결과, 영향받은/반환된 행 수)를 돌려준다."""
        start = time.perf_counter()
        entry: dict[str, Any] = {
            "at": datetime.now().strftime("%H:%M:%S"),
            "label": label or "",
            "sql": sql.strip(),
            "params": params,
        }
        try:
            result, entry["rows"] = self.run(fn)
            return result
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
            raise
        finally:
            entry["ms"] = round((time.perf_counter() - start) * 1000)
            self.log.append(entry)

    def query(self, sql: str, params: Any = None, *, label: str | None = None) -> list[dict[str, Any]]:
        def fn(cur):
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description or []]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]
            return rows, len(rows)

        return self._logged(label, sql, params, fn)

    def execute(self, sql: str, params: Any = None, *, label: str | None = None) -> int:
        """실행 후 lastrowid를 돌려준다 (AUTO_RANDOM 키 INSERT 후 ID 확인용)."""
        def fn(cur):
            affected = cur.execute(sql, params)
            return cur.lastrowid, affected

        return self._logged(label, sql, params, fn)

    def executemany(self, sql: str, rows: Sequence[Sequence[Any]], *, label: str | None = None) -> int:
        def fn(cur):
            affected = cur.executemany(sql, rows) or 0
            return affected, affected

        return self._logged(label, sql, f"{len(rows)} rows", fn)


@lru_cache
def get_db() -> Database:
    """프로세스 전체에서 공유하는 Database (화면, 그래프 노드, 기록). 체크포인터는 연결을 따로 쓴다."""
    return Database()
