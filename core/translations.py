"""표시용 번역과 TiDB 번역 캐시(translations 테이블).

화면(ui/common.py)과 백그라운드 작업(승인·거절 뒤 mem9 기억 정리)이 함께 쓴다.
저장된 원본(mem9 기억, 분석 결과 등)은 바꾸지 않고, 번역문만 캐시에 쌓는다.
"""
from __future__ import annotations

import hashlib

from core.db import get_db
from core.llm import needs_translation, translate

LANGS = ("ko", "en")


def src_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cached_translate(texts: list[str], target: str) -> list[str]:
    """캐시에서 먼저 찾고, 없는 문장만 Claude로 번역해 저장한다."""
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
    """두 언어의 번역을 미리 만들어 캐시에 넣는다. 실패해도 화면에서 다시 시도하므로 넘어간다."""
    for target in LANGS:
        try:
            cached_translate(texts, target)
        except Exception:
            pass
