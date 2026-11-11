"""Environment variable settings.

Locally it reads .env in the project root; on Streamlit Community Cloud it copies the top-level Secrets keys
into environment variables, so the same code works in both. Streamlit only exposes top-level keys as
environment variables on the first access to st.secrets, so rather than rely on that order we copy them here
(sections such as [auth] are not copied).
ANTHROPIC_API_KEY and LANGSMITH_* are read by LangChain directly from the environment.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _load_streamlit_secrets() -> None:
    """Copies top-level Streamlit Secrets values into env vars. Existing env vars (.env etc.) are not overwritten."""
    try:
        import streamlit as st

        items = st.secrets.to_dict().items()
    except Exception:          # local or script run without a Secrets file
        return
    for key, value in items:
        if not isinstance(value, dict):
            os.environ.setdefault(key, str(value))


_load_streamlit_secrets()

REQUIRED = (
    "TIDB_HOST",
    "TIDB_USER",
    "TIDB_PASSWORD",
    "MEM9_API_KEY",
    "ANTHROPIC_API_KEY",
)


@dataclass(frozen=True)
class Settings:
    tidb_host: str
    tidb_port: int
    tidb_user: str
    tidb_password: str
    tidb_database: str
    mem9_api_url: str
    mem9_api_key: str
    llm_model: str


def missing(*names: str) -> list[str]:
    """Names of environment variables whose value is empty. With no arguments, checks all of REQUIRED."""
    return [n for n in (names or REQUIRED) if not os.environ.get(n)]


@lru_cache
def get_settings() -> Settings:
    env = os.environ
    return Settings(
        tidb_host=env.get("TIDB_HOST", ""),
        tidb_port=int(env.get("TIDB_PORT", "4000")),
        tidb_user=env.get("TIDB_USER", ""),
        tidb_password=env.get("TIDB_PASSWORD", ""),
        tidb_database=env.get("TIDB_DATABASE", "invest_demo"),
        mem9_api_url=env.get("MEM9_API_URL", "https://api.mem9.ai").rstrip("/"),
        mem9_api_key=env.get("MEM9_API_KEY", ""),
        llm_model=env.get("LLM_MODEL", "claude-haiku-5-5"),
    )
