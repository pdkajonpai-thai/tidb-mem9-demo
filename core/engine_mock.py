"""실행 엔진 시뮬레이션 (연동할 기존 자동화 엔진 자리).

연동할 엔진이 거래소 API 기반이든 온체인 기반이든 맞도록, 두 경로를 같은 API로 받는다.
주문 단계의 chain이 거래소 계정(binance 등)이면 거래소 API 경로, 네트워크(arbitrum 등)면 온체인 경로다.

엔진 API는 두 가지다.
- simulate(): 주문을 실제로 보내지 않고 예상 체결량·수수료·연 예상 수익을 계산한다.
- submit(): 주문을 PENDING → SUBMITTED → CONFIRMED로 진행하고 가짜 체결 ID(tx_hash)를 기록한다.
실행 agent의 execute_order tool이 이 API를 호출한다. 실제 연동 시 이 모듈만 바꾼다.
호출은 REST 요청처럼 calls에 남겨 Behind the scenes의 Engine calls 탭에서 요청·응답 JSON을 보여준다.
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
GAS_USD = {"ethereum": 4.5, "arbitrum": 0.15, "base": 0.05, "solana": 0.01, "bitcoin": 2.0, "kaia": 0.01}  # 합성 값
CEX_ACCOUNTS = {"upbit", "binance", "bybit", "okx", "coinbase", "bithumb", "coinone", "korbit"}
EXCHANGE_API, ONCHAIN = "exchange_api", "onchain"
SIMULATE = "/v1/orders/simulate"
ORDERS = "/v1/orders"

calls = CallLog(maxlen=200)     # Behind the scenes 화면용 엔진 호출 기록 (작업 공간별)


def _log(path: str, body: dict, response: dict, start: float) -> None:
    calls.append({"method": "POST", "path": path, "body": body, "response": response, "status": 200,
                  "ms": round((time.perf_counter() - start) * 1000)})


def route(chain: str) -> str:
    """실행 경로 코드 (엔진 API 응답에 그대로 나가므로 언어와 무관한 값). 화면 표시는 t("route." + 코드)."""
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
            # 최악의 경우(슬리피지 한도 전부 소진)를 기준으로 계산
            received = usd / p[s["to_asset"].upper()] * (1 - s["slippage_bps"] / 10_000)
        # 거래소 계정 안 예치·스테이킹은 네트워크 수수료가 없다 (거래 수수료는 데모에서 생략)
        gas = 0.0 if route(s["chain"]) == EXCHANGE_API else GAS_USD.get(s["chain"].lower(), 1.0)
        gas_total += gas
        rows.append({
            "action": s["action"], "amount": s["amount"], "from_asset": s["from_asset"],
            "to_asset": s.get("to_asset"), "usd": round(usd, 2) if usd is not None else None,
            "min_received": round(received, 6) if received is not None else None, "gas_usd": gas,
            "route": route(s["chain"]),
        })
    # 포지션 = 첫 단계 입력 자산으로 들어가는 금액 합계 (LP는 스왑분 + 예치분)
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
    # 승인 후 재개하면 tool이 처음부터 다시 실행되므로, 같은 명령의 시뮬레이션은 한 번만 기록한다
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
    # 19자리 주문 ID는 브라우저 JSON 표시에서 반올림되므로 기록에는 문자열로 남긴다
    _log(ORDERS, command, {**order, "order_id": str(order_id), "status_history": ["PENDING", "SUBMITTED", "CONFIRMED"]},
         start)
    return order
