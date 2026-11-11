"""Login identity, client IP, and access/usage records (access_logs).

- Identity: the st.user logged in via st.login (Google OIDC). Only in local testing is this bypassed with
  DEMO_AUTH=off, using DEMO_DEV_EMAIL as the identity (not put in the deployed Secrets).
- IPs are not recorded. Streamlit Community Cloud does not pass the real client IP to the app
  (checked 2026-10-29: X-Forwarded-For only carries internal 10.x addresses). The ip and forwarded_for
  columns of access_logs are kept, left empty, for a future move to hosting that passes the IP.
- Records are not deleted by the demo reset.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

import streamlit as st

from core.db import get_db

logger = logging.getLogger(__name__)


def auth_bypassed() -> bool:
    return os.environ.get("DEMO_AUTH", "").lower() == "off"


def auth_configured() -> bool:
    try:
        return "auth" in st.secrets
    except Exception:
        return False


def is_logged_in() -> bool:
    return auth_bypassed() or bool(getattr(st.user, "is_logged_in", False))


def identity() -> dict[str, str | None]:
    if auth_bypassed():
        email = os.environ.get("DEMO_DEV_EMAIL", "dev@localhost")
        return {"email": email, "name": "local-dev"}
    if getattr(st.user, "is_logged_in", False):
        return {"email": st.user.get("email"), "name": st.user.get("name")}
    return {"email": None, "name": None}


def admin_emails() -> set[str]:
    raw = os.environ.get("ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin() -> bool:
    email = (identity()["email"] or "").lower()
    return bool(email) and email in admin_emails()


def session_id() -> str:
    if "_audit_session" not in st.session_state:
        st.session_state["_audit_session"] = uuid.uuid4().hex[:16]
    return st.session_state["_audit_session"]


def _fit(value: Any, size: int) -> str | None:
    """String trimmed to the column length (so a record is not lost when an unexpected value arrives)."""
    return None if value is None else str(value)[:size]


def log_event(event: str, detail: dict[str, Any] | None = None) -> None:
    """Writes one row to access_logs. On failure the demo keeps going and only a warning is logged."""
    who = identity()
    try:
        agent = st.context.headers.get("User-Agent")
    except Exception:
        agent = None
    try:
        get_db().execute(
            """INSERT INTO access_logs (event, email, name, ip, forwarded_for, user_agent, session_id, detail)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (event, _fit(who["email"], 255), _fit(who["name"], 255), None, None,
             _fit(agent, 512), session_id(), json.dumps(detail or {}, ensure_ascii=False, default=str)),
            label="기록: 이벤트 저장")
    except Exception:
        # Don't let a failed record block the demo, but log it so missing records can be noticed
        logger.warning("access_logs 기록 실패 (event=%s)", event, exc_info=True)


def log_once(event: str, detail: dict[str, Any] | None = None) -> None:
    """Records only once per session (visit, login)."""
    key = f"_audit_logged_{event}"
    if not st.session_state.get(key):
        st.session_state[key] = True
        log_event(event, detail)
