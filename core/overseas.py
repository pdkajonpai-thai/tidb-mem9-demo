"""Month-end balance info for overseas exchanges (the overseas account reporting reference scene).

Aggregates month-end overseas exchange balances with SQL and shows the distance to the overseas account
reporting threshold (month-end total of KRW 500 million). All calculation is done in SQL with no LLM involved.
The screen text (ui.i18n "overseas.notice") stays factual only; whether reporting is required is left to a tax
professional.
"""
from __future__ import annotations

from typing import Any

from core.db import Database

THRESHOLD_KRW = 500_000_000


def overseas_locations(db: Database) -> set[str]:
    """Location names of overseas exchange accounts (e.g. binance, bybit). Compared with an order step's chain."""
    rows = db.query("SELECT LOWER(name) AS loc FROM exchanges WHERE region = 'GLOBAL'",
                    label="해외 잔고: 해외 거래소 목록")
    return {r["loc"] for r in rows}


def overseas_status(db: Database, user_id: int) -> dict[str, Any] | None:
    """Monthly overseas exchange balance totals for the latest year, the peak, and the amount left to the threshold.
    None if there are no overseas balances."""
    months = db.query(
        """
        SELECT s.month_end, SUM(s.krw_value) AS total_krw
        FROM balance_snapshots s
        JOIN exchanges e ON e.id = s.exchange_id AND e.region = 'GLOBAL'
        WHERE s.user_id = %s
          AND YEAR(s.month_end) = (SELECT MAX(YEAR(month_end)) FROM balance_snapshots WHERE user_id = %s)
        GROUP BY s.month_end
        ORDER BY s.month_end
        """, (user_id, user_id), label="해외 잔고: 월별 합계")
    if not months:
        return None
    latest = months[-1]
    by_exchange = db.query(
        """
        SELECT e.name AS exchange, s.asset_symbol, s.amount, s.krw_value
        FROM balance_snapshots s JOIN exchanges e ON e.id = s.exchange_id
        WHERE s.user_id = %s AND s.month_end = %s AND e.region = 'GLOBAL'
        ORDER BY s.krw_value DESC
        """, (user_id, latest["month_end"]), label="해외 잔고: 최근 월말 거래소별")
    peak = max(months, key=lambda m: m["total_krw"])
    return {
        "months": [{"month_end": m["month_end"].isoformat(), "total_krw": int(m["total_krw"])} for m in months],
        "latest_month": latest["month_end"].isoformat(),
        "latest_krw": int(latest["total_krw"]),
        "peak_month": peak["month_end"].isoformat(),
        "peak_krw": int(peak["total_krw"]),
        "threshold_krw": THRESHOLD_KRW,
        "gap_krw": THRESHOLD_KRW - int(peak["total_krw"]),
        "exceeded": int(peak["total_krw"]) > THRESHOLD_KRW,
        "by_exchange": [{**r, "amount": float(r["amount"]), "krw_value": int(r["krw_value"])} for r in by_exchange],
    }
