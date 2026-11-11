"""Execution engine simulation (a stand-in for the existing automation engine to be integrated).

Both paths are accepted through the same API, so it fits whether the engine is exchange-API based or on-chain.
If an order step's chain is an exchange account (binance etc.) it takes the exchange API path; if it is a
network (arbitrum etc.) it takes the on-chain path.

The engine API has two calls.
- simulate(): computes expected fill amount, fees, and expected annual yield without actually sending the order.
- submit(): advances the order PENDING → SUBMITTED → CONFIRMED and records a fake fill ID (tx_hash).
The execution agent's execute_order tool calls this API. For a real integration, only this module changes.
Calls are kept in calls like REST requests, so the Engine calls tab of Behind the scenes shows request/response JSON.
"""
from __future__ import annotations

import hashlib
import json
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.call_log import CallLog, current_workspace
from core.db import Database

HOLDINGS = Path(__file__).resolve().parent.parent / "data" / "holdings.json"
GAS_USD = {"ethereum": 4.5, "arbitrum": 0.15, "base": 0.05, "solana": 0.01, "bitcoin": 2.0, "kaia": 0.01}  # synthetic values
CEX_ACCOUNTS = {"upbit", "binance", "bybit", "okx", "coinbase", "bithumb", "coinone", "korbit"}
EXCHANGE_API, ONCHAIN = "exchange_api", "onchain"
SIMULATE = "/v1/orders/simulate"
ORDERS = "/v1/orders"

calls = CallLog(maxlen=200)     # engine call records for the Behind the scenes screen (per workspace)


def _log(path: str, body: dict, response: dict, start: float) -> None:
    calls.append({"method": "POST", "path": path, "body": body, "response": response, "status": 200,
                  "ms": round((time.perf_counter() - start) * 1000)})


def route(chain: str) -> str:
    """Execution route code (language-independent: it goes out as is in the engine API response).
    Displayed via t("route." + code)."""
    return EXCHANGE_API if chain.lower() in CEX_ACCOUNTS else ONCHAIN


@lru_cache
def prices() -> dict[str, float]:
    return json.loads(HOLDINGS.read_text(encoding="utf-8"))["prices"]


def simulate(steps: list[dict[str, Any]], expected_apy: float | None = None, *,
             command_id: str | None = None) -> dict[str, Any]:
    start = time.perf_counter()
    p = prices()
    rows, gas_total = [], 0.0
    for s in steps:
        from_price = p.get(s["from_asset"].upper())
        usd = s["amount"] * from_price if from_price else None
        received = None
        if s["action"] == "swap" and s.get("to_asset") and usd is not None and p.get(s["to_asset"].upper()):
            # computed for the worst case (the full slippage limit used up)
            received = usd / p[s["to_asset"].upper()] * (1 - s["slippage_bps"] / 10_000)
        # deposits/staking inside an exchange account have no network fee (trading fees are omitted in the demo)
        gas = 0.0 if route(s["chain"]) == EXCHANGE_API else GAS_USD.get(s["chain"].lower(), 1.0)
        gas_total += gas
        rows.append({
            "action": s["action"], "amount": s["amount"], "from_asset": s["from_asset"],
            "to_asset": s.get("to_asset"), "usd": round(usd, 2) if usd is not None else None,
            "min_received": round(received, 6) if received is not None else None, "gas_usd": gas,
            "route": route(s["chain"]),
        })
    # position = total amount going in as the first step's input asset (for LP: swap part + deposit part)
    first = steps[0] if steps else None
    input_rows = [r for r, s in zip(rows, steps)
                  if first and s["from_asset"].upper() == first["from_asset"].upper() and r["usd"] is not None]
    position_usd = round(sum(r["usd"] for r in input_rows), 2) if input_rows else None
    result = {
        "steps": rows,
        "routes": sorted({r["route"] for r in rows}),
        "gas_usd": round(gas_total, 2),
        "position_usd": position_usd,
        "expected_yearly_usd": round(position_usd * expected_apy / 100, 2)
        if position_usd is not None and expected_apy else None,
    }
    # On resume after approval the tool reruns from the start, so a simulation of the same command is recorded once
    ws = current_workspace.get()
    if not command_id or not any(c["path"] == SIMULATE and c["body"].get("command_id") == command_id
                                 for c in calls.recent(ws)):
        _log(SIMULATE, {"command_id": command_id, "steps": steps, "expected_apy": expected_apy}, result, start)
    return result


def submit(db: Database, recommendation_id: int, command: dict[str, Any], *, step_delay: float = 0.6) -> dict:
    start = time.perf_counter()
    payload = json.dumps(command, ensure_ascii=False)
    order_id = db.execute(
        "INSERT INTO execution_orders (recommendation_id, command, status) VALUES (%s, %s, 'PENDING')",
        (recommendation_id, payload), label="실행: 주문 생성 (PENDING)")
    time.sleep(step_delay)
    db.execute("UPDATE execution_orders SET status = 'SUBMITTED' WHERE id = %s",
               (order_id,), label="실행: 엔진 전달 (SUBMITTED)")
    time.sleep(step_delay)
    tx_hash = "0x" + hashlib.sha256(f"{order_id}:{payload}".encode()).hexdigest()
    db.execute("UPDATE execution_orders SET status = 'CONFIRMED', tx_hash = %s WHERE id = %s",
               (tx_hash, order_id), label="실행: 체결 확인 (CONFIRMED)")
    order = {"order_id": order_id, "status": "CONFIRMED", "tx_hash": tx_hash}
    # 19-digit order IDs get rounded in the browser's JSON display, so they are recorded as strings
    _log(ORDERS, command, {**order, "order_id": str(order_id), "status_history": ["PENDING", "SUBMITTED", "CONFIRMED"]},
         start)
    return order
