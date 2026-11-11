"""Display translations and the TiDB translation cache (translations table).

Shared by the screens (ui/common.py) and background jobs (mem9 memory update after approve/reject).
Stored originals (mem9 memories, analysis results, etc.) are not changed; only translations go into the cache.
"""
from __future__ import annotations

import hashlib

from core.db import get_db
from core.llm import needs_translation, translate

LANGS = ("ko", "en")


def src_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached_translate(texts: list[str], target: str) -> list[str]:
    """Looks in the cache first, and translates with Claude and stores only the sentences not found."""
    need = list(dict.fromkeys(x for x in texts if needs_translation(x, target)))
    if not need:
        return list(texts)
    db = get_db()
    hashes = {x: src_hash(x) for x in need}
    rows = db.query(
        f"SELECT src_hash, text FROM translations WHERE lang = %s AND src_hash IN ({', '.join(['%s'] * len(need))})",
        (target, *hashes.values()), label="번역: 캐시 조회")
    cached = {r["src_hash"]: r["text"] for r in rows}
    mapping = {x: cached[h] for x, h in hashes.items() if h in cached}
    missing = [x for x in need if x not in mapping]
    if missing:
        done = translate(missing, target)
        new = [(hashes[x], target, x, y) for x, y in zip(missing, done) if y != x]
        if new:
            db.executemany(
                "INSERT INTO translations (src_hash, lang, src, text) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE text = VALUES(text)", new, label="번역: 캐시 저장")
        mapping.update(zip(missing, done))
    return [mapping.get(x, x) for x in texts]


def prepare(texts: list[str]) -> None:
    """Pre-builds translations in both languages into the cache. Failures are ignored since the screen retries."""
    for target in LANGS:
        try:
            cached_translate(texts, target)
        except Exception:
            pass
