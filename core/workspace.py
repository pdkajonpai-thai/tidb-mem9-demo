"""Per-visitor workspaces: analysis results, recommendations, checkpoints, and mem9 memories are kept separate
per Google account.

- The workspace ID is the first 12 characters of the email's SHA-256 (the email itself is not stored).
- Users A/B/C's profiles, holdings, accounts, and balance history use the same seed in every workspace
  (orders are mocked, so these do not change).
- A workspace used for the first time starts by writing onboarding memories to mem9. A reset also only restores
  its own workspace.
"""
from __future__ import annotations

import hashlib
import json

from core.config import ROOT
from core.db import Database, get_db
from core.mem9_client import Mem9Client, get_mem9, user_app_id


def workspace_id(email: str) -> str:
    return "w" + hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]


def thread_prefix(workspace: str) -> str:
    """Prefix of recommendation thread IDs. Used on reset to find this workspace's checkpoints."""
    return f"rec_{workspace}_"


def _personas() -> list[dict]:
    return json.loads((ROOT / "data" / "users.json").read_text(encoding="utf-8"))


def seed_memories(workspace: str, mem9: Mem9Client | None = None) -> dict[str, int]:
    """Clears the per-user memories of the workspace and records onboarding preferences as pinned memories."""
    mem9 = mem9 or get_mem9()
    result = {}
    for user in _personas():
        app_id = user_app_id(user["id"], workspace)
        mem9.clear(app_id)
        for content in user["onboarding_memories"]:
            mem9.add_pinned(app_id, content, tags=["preference"])
        result[app_id] = len(user["onboarding_memories"])
    return result


def ensure_workspace(workspace: str, db: Database | None = None) -> bool:
    """Prepares mem9 memories if the workspace is used for the first time. True if newly created."""
    db = db or get_db()
    if db.query("SELECT 1 FROM workspaces WHERE id = %s", (workspace,), label="작업 공간: 확인"):
        return False
    seed_memories(workspace)
    db.execute("INSERT IGNORE INTO workspaces (id) VALUES (%s)", (workspace,), label="작업 공간: 생성")
    return True


def clear_analysis(workspace: str, db: Database | None = None) -> None:
    """Deletes this workspace's analysis results and returns source posts to the unanalyzed state."""
    db = db or get_db()
    db.execute("DELETE FROM opportunities WHERE workspace = %s", (workspace,), label="분석: 초기화")
    db.execute("DELETE FROM analyzed_signals WHERE workspace = %s", (workspace,), label="분석: 초기화")


def reset_workspace(workspace: str, db: Database | None = None) -> None:
    """Resets only this workspace to its initial state: analysis results, recommendations/orders, checkpoints,
    mem9 memories."""
    db = db or get_db()
    clear_analysis(workspace, db)
    db.execute(
        """DELETE e FROM execution_orders e JOIN recommendations r ON r.id = e.recommendation_id
           WHERE r.workspace = %s""", (workspace,), label="초기화: 주문")
    db.execute("DELETE FROM recommendations WHERE workspace = %s", (workspace,), label="초기화: 추천")
    pattern = thread_prefix(workspace).replace("_", r"\_") + "%"
    for table in ("lg_checkpoint_writes", "lg_checkpoint_blobs", "lg_checkpoints"):
        db.execute(f"DELETE FROM {table} WHERE thread_id LIKE %s", (pattern,), label="초기화: 체크포인트")
    seed_memories(workspace)
    db.execute(
        "INSERT INTO workspaces (id, reset_at) VALUES (%s, NOW()) ON DUPLICATE KEY UPDATE reset_at = NOW()",
        (workspace,), label="초기화: 작업 공간")
