"""TiDB connection (PyMySQL, TLS), with SQL logging.

TiDB Cloud Starter requires TLS; the CA comes from the certifi bundle.
A new TLS connection takes about 1.3 seconds (measured in the Tokyo region), so the whole app reuses
one connection guarded by a Lock. On a connection error it reconnects and retries once.

No ORM is used. Showing the hand-written SQL as is in the SQL Inspector is the point of the demo.
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
        self.log = CallLog(maxlen=200)   # for the SQL Inspector (per workspace)

    def run(self, fn: Callable[[Any], T], *, transaction: bool = False) -> T:
        """Runs a function that works on a cursor. Nothing is logged (e.g. the checkpointer's internal SQL)."""
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
                    # Connection dropped (idle timeout etc.): discard it, reconnect, and retry once only
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
        """fn returns (result, number of affected/returned rows)."""
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
        """Returns lastrowid after execution (to get the ID after an INSERT with an AUTO_RANDOM key)."""
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
    """Database shared by the whole process (screens, graph nodes, records). The checkpointer has its own connection."""
    return Database()
