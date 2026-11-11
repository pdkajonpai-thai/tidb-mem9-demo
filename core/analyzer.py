"""Node functions of the analysis graph (analysis_graph).

load_signals → analyze_signal (one Send per signal) → map_entities → save_opportunities
See ARCHITECTURE.md section 2.4.1.
"""
from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage

from core.db import Database, get_db
from core.llm import structured
from core.schemas import SignalAnalysis

STRATEGY_IDS = {"swap": 1, "lend": 2, "stake": 3, "lp": 4}

SYSTEM = """당신은 DeFi 투자 정보 분석가입니다. 인플루언서 게시물이나 공지 원문 1건을 읽고, 사용자가 실제로 실행할 수 있는 투자 기회를 구조화합니다.

규칙
- 원문에 근거한 기회만 만듭니다. 원문에 없는 수익률이나 프로토콜을 지어내지 않습니다.
- 경고, 시장 논평, "추천하지 않는다"는 글처럼 실행할 기회가 없으면 is_actionable=false로 두고 reason에 이유를 적습니다. opportunities는 빈 목록입니다.
- protocol과 chain은 '지원 프로토콜' 목록에 있는 조합만 씁니다. 목록에 없으면 기회를 만들지 않습니다.
- chain은 자산이 있는 위치입니다. 온체인 상품은 네트워크(arbitrum 등), 거래소 예치·스테이킹 상품은 거래소 계정 이름(upbit, binance, bybit, okx 등)입니다. 거래소 상품의 위험은 거래소 보관(상대방) 위험으로 보고 risk_level 1~2로 둡니다.
- asset은 사용자가 넣는 자산 하나이며 '지원 자산' 목록의 기호만 씁니다. LP는 페어 중 스테이블코인 쪽(ETH-USDC면 USDC), 스왑은 파는 자산, 스테이킹은 맡기는 자산(ETH, SOL)입니다.
- risk_level (1~5): 1 = 감사받은 대형 렌딩·스테이킹, 원금 가격 변동 거의 없음 / 2 = 스테이블 풀, 추가 컨트랙트 계층 / 3 = LP 비영구적 손실, 고정 기간 잠김 / 4 = 트레이더 손익 노출 / 5 = 감사 없음, 러그풀 의심, 만기 가치 0 위험.
- confidence (0~1): 원문이 구체적인 수치와 근거를 줄수록 높게.
- title: 한국어 25자 이내. 예: "Aave v3 아비트럼 USDC 예치"
- summary: 한국어 2~3문장. 무엇을, 어디서, 기대 수익과 주요 위험을 씁니다. 의미 검색에 쓰이므로 "스테이블코인 이자", "유동성 공급", "변동성", "비영구적 손실" 같은 핵심어를 분명히 씁니다.
- rationale: 이렇게 판단한 근거 1~2문장 (한국어)."""


class SignalTask(TypedDict):
    """Input passed to analyze_signal for each source post via Send."""
    signal: dict[str, Any]
    catalog: dict[str, Any]


def load_catalog(db: Database) -> dict[str, Any]:
    protocols = db.query(
        "SELECT id, name, chain, audited, risk_score FROM protocols ORDER BY id", label="분석: 프로토콜 목록")
    assets = db.query("SELECT symbol, chain FROM assets ORDER BY symbol, chain", label="분석: 자산 목록")
    return {"protocols": protocols, "assets": assets}


def catalog_prompt(catalog: dict[str, Any]) -> str:
    protocols = "\n".join(
        f"- {p['name']} / {p['chain']} (감사 {'O' if p['audited'] else 'X'}, 기본 리스크 {p['risk_score']})"
        for p in catalog["protocols"])
    assets = ", ".join(sorted({a["symbol"] for a in catalog["assets"]}))
    return f"지원 프로토콜 (이름 / 체인)\n{protocols}\n\n지원 자산: {assets}"


# ---- Nodes ----

def load_signals(state: dict) -> dict:
    db = get_db()
    signals = db.query(
        """SELECT id, source, author, content, posted_at FROM raw_signals
           WHERE id NOT IN (SELECT signal_id FROM analyzed_signals WHERE workspace = %s) ORDER BY id""",
        (state["workspace"],), label="분석: 미분석 원문 조회")
    for s in signals:
        s["posted_at"] = str(s["posted_at"])
    return {"signals": signals, "catalog": load_catalog(db)}


def analyze_signal(task: SignalTask) -> dict:
    s = task["signal"]
    result: SignalAnalysis = structured(SignalAnalysis, effort="low").invoke([
        SystemMessage(SYSTEM),
        HumanMessage(
            f"{catalog_prompt(task['catalog'])}\n\n원문 (#{s['id']}, {s['source']}, {s['author']}, {s['posted_at']})\n"
            f"{s['content']}"),
    ])
    if not result.is_actionable or not result.opportunities:
        return {"skipped": [{"signal_id": s["id"], "reason": result.reason or "실행 가능한 기회 없음"}]}
    return {"drafts": [{"signal_id": s["id"], **o.model_dump()} for o in result.opportunities]}


def map_entities(state: dict) -> dict:
    """Maps names written by the LLM to DB IDs, clamps ranges, and merges identical opportunities into one."""
    catalog = state["catalog"]
    protocol_ids = {(p["name"].lower(), p["chain"].lower()): p["id"] for p in catalog["protocols"]}
    assets = {(a["symbol"].upper(), a["chain"].lower()) for a in catalog["assets"]}

    best: dict[tuple, dict] = {}
    skipped = []
    for d in state.get("drafts", []):
        chain = d["chain"].lower()
        asset = d["asset"].upper()
        protocol_id = protocol_ids.get((d["protocol"].lower(), chain))
        if protocol_id is None:
            skipped.append({"signal_id": d["signal_id"], "reason": f"지원하지 않는 프로토콜: {d['protocol']}/{chain}"})
            continue
        if (asset, chain) not in assets:
            skipped.append({"signal_id": d["signal_id"], "reason": f"지원하지 않는 자산: {asset}/{chain}"})
            continue
        opp = {
            **d,
            "asset": asset,
            "chain": chain,
            "protocol_id": protocol_id,
            "strategy_id": STRATEGY_IDS[d["action_type"]],
            "risk_level": min(max(int(d["risk_level"]), 1), 5),
            "confidence": round(min(max(float(d["confidence"]), 0.0), 1.0), 2),
            "summary": d["summary"][:1500],
        }
        key = (protocol_id, asset, chain, d["action_type"])
        kept = best.get(key)
        if kept is None or opp["confidence"] > kept["confidence"]:
            if kept is not None:
                skipped.append({"signal_id": kept["signal_id"], "reason": f"중복: 원문 #{d['signal_id']}과 같은 기회"})
            best[key] = opp
        else:
            skipped.append({"signal_id": d["signal_id"], "reason": f"중복: 원문 #{kept['signal_id']}과 같은 기회"})
    return {"opportunities": list(best.values()), "skipped": skipped}


def save_opportunities(state: dict) -> dict:
    db = get_db()
    opps = state.get("opportunities", [])
    if opps:
        db.executemany(
            """
            INSERT INTO opportunities
              (workspace, signal_id, title, asset, chain, protocol_id, strategy_id, action_type,
               expected_apy, risk_level, horizon, confidence, summary, rationale)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(state["workspace"], o["signal_id"], o["title"][:128], o["asset"], o["chain"], o["protocol_id"], o["strategy_id"],
              o["action_type"], o["expected_apy"], o["risk_level"], o["horizon"], o["confidence"],
              o["summary"], o["rationale"]) for o in opps],
            label="분석: 기회 저장 (summary 자동 임베딩)")
    ids = [s["id"] for s in state.get("signals", [])]
    if ids:
        db.executemany("INSERT IGNORE INTO analyzed_signals (workspace, signal_id) VALUES (%s, %s)",
                       [(state["workspace"], i) for i in ids], label="분석: 원문 처리 표시")
    return {"saved": len(opps)}

