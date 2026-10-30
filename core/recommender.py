"""추천·승인 그래프(recommend_graph) 노드 함수와 실행 agent의 tool.

recall_memory → build_intent → search_candidates → recommend → validate → save_recommendations
→ choose(interrupt: 실행할 추천 선택) → exec_agent ⇄ tools(execute_order: 검증 → 시뮬레이션
→ interrupt: 주문 승인 → 엔진 실행 또는 거절 기록)
ARCHITECTURE.md 2.4.2절 참고.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Annotated, Any

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command, interrupt

from core import engine_mock, memory_jobs
from core.config import get_settings
from core.db import get_db
from core.llm import get_llm, output_language_rule, structured
from core.mem9_client import get_mem9, user_app_id
from core.overseas import overseas_locations, overseas_status
from core.schemas import ExecutionCommand, ExecutionStep, ExtractedFacts, RecommendationSet, SearchIntent

MAX_CARDS = 3
MAX_ATTEMPTS = 2           # 검증 실패 시 1회 재생성
MAX_TOOL_CALLS = 3         # 실행 agent가 execute_order를 부를 수 있는 최대 횟수 (검증 오류 수정 포함)
MAX_SLIPPAGE_BPS = 100
INSIGHT_WAIT_SECONDS = 15   # 성공한 추출은 3~7초 안에 끝났다. 넘으면 Claude 대체 경로
COMMAND_TTL = timedelta(minutes=30)

# 핵심 쇼케이스 쿼리 (ARCHITECTURE.md 3.3): 벡터 유사도 + 보유 자산·거래소 체인·리스크 한도를 한 SQL로
HYBRID_SQL = """
SELECT o.id, o.title, o.asset, o.chain, p.name AS protocol, p.audited, o.protocol_id, o.strategy_id,
       o.action_type, o.expected_apy, o.risk_level, o.horizon, o.summary,
       h.amount AS holding_amount, h.usd_value AS holding_usd,
       VEC_EMBED_COSINE_DISTANCE(o.summary_vec, %(intent)s) AS distance
FROM opportunities o
JOIN protocols p      ON p.id = o.protocol_id
JOIN user_holdings h  ON h.user_id = %(uid)s
                     AND h.asset_symbol = o.asset
                     AND h.chain = o.chain
WHERE o.workspace = %(ws)s
  AND o.status = 'ACTIVE'
  AND o.chain IN (
        SELECT ec.chain
        FROM user_exchange_accounts ua
        JOIN exchange_chains ec ON ec.exchange_id = ua.exchange_id
        WHERE ua.user_id = %(uid)s AND ua.status = 'ACTIVE')
  AND o.risk_level <= (SELECT max_risk_level FROM users WHERE id = %(uid)s)
ORDER BY distance
LIMIT 10
"""

INTENT_SYSTEM = """사용자 프로필, 보유 자산, 장기 기억을 보고 투자 기회 벡터 검색에 쓸 검색 문장 1개를 출력 언어로 만듭니다.

규칙
- 사용자에게 맞는 기회의 성격(수익 방식, 위험 수준, 자산 종류)을 30~60자로 묘사합니다.
- 기억에 수익 목표가 있으면 그 목표 수준을 문장에 반영합니다.
- 기억에 사용자가 피하거나 거절한 유형이 있으면 그 반대 성격을 분명히 씁니다. 예: "가격 변동성이 낮은 스테이블코인 이자 수익"
- 기억에 없는 위험 회피 성향을 추측해 덧붙이지 않습니다.
- 기억의 언어와 상관없이 출력 언어로 씁니다. 특정 프로토콜 이름은 넣지 않습니다.
- reason에는 어떤 프로필·기억 때문에 이 문장을 만들었는지 한 문장으로 씁니다."""

RECOMMEND_SYSTEM = """당신은 개인 DeFi 투자 도우미입니다. 후보 목록에서 사용자에게 맞는 기회를 최대 3개 골라 순위를 매기고 실행 명령 초안을 만듭니다.

선택과 근거
- candidate_no는 후보 목록의 번호만 씁니다. 같은 후보를 두 번 고르지 않습니다.
- 장기 기억에 수익 목표가 있으면, 리스크 한도 안에서 그 목표를 달성할 수 있는 후보를 최소 1개 포함합니다.
- 단, 사용자가 거절했거나 피하고 싶다고 한 유형은 수익 목표보다 우선합니다. 그런 후보는 고르지 않고, 목표보다 수익이 낮은 추천이 된 이유를 rationale에 설명합니다.
- 위험이나 변동성을 부담스러워한다는 기억이 있으면 리스크가 낮은 후보를 위 순위에 둡니다.
- 근거로 쓴 기억의 번호(M1 → 1)를 memory_nos에 넣고, rationale에서 자연스럽게 언급합니다. 예: "지난번 변동성이 큰 상품을 거절하셨기 때문에". 기억의 언어와 상관없이 출력 언어로 풀어 씁니다.
- rationale에는 "M3", "(1)" 같은 기억 번호를 쓰지 않습니다. 번호는 memory_nos에만 넣습니다.
- rationale: 2~3문장. 기대 수익, 위험, 이 사용자에게 맞는 이유.
- headline: 카드 제목 한 줄 (한국어 30자, 영어 60자 이내).

실행 명령 (command)
- steps는 실제 실행 순서입니다. chain은 후보의 위치(네트워크 또는 거래소 계정 이름)입니다.
- exchange는 사용자의 거래소 계정 이름이거나, 지갑으로 직접 쓰는 해당 후보의 프로토콜 이름입니다. 거래소 상품(chain이 거래소 계정)은 그 거래소 이름을 씁니다.
- 첫 단계의 from_asset은 후보의 asset이고, amount는 그 자산 보유 수량 이하입니다. 위험이 낮으면 보유량의 30~50%, 높으면 10~20%를 씁니다.
- lend: deposit 1단계 / stake: stake 1단계 / swap: swap 1단계.
- lp: 넣을 총액 X를 두 단계로 나눕니다. ① swap: from_asset=입력 자산, to_asset=페어 자산, amount=X/2 ② deposit: from_asset=입력 자산, to_asset=페어 자산, amount=X/2. 두 단계 amount는 같고 합계가 X입니다.
- slippage_bps: 스테이블코인 단계는 30 이하, 그 외는 100 이하. max_slippage_bps는 단계 중 최댓값입니다.
- 데모용 추천입니다. 수익을 과장하지 않습니다."""

FACTS_SYSTEM = """사용자가 투자 추천을 승인하거나 거절하며 남긴 이유에서, 앞으로 추천할 때 반영할 선호나 회피 사실을 1~3개 뽑습니다.
각 사실은 "~을 피함", "~을 선호함" 형태의 짧은 문장(출력 언어)입니다. 사유에 없는 내용은 만들지 않습니다."""


def plain(value: Any) -> Any:
    """DB 값(Decimal, datetime)을 체크포인트·JSON에 저장할 수 있는 값으로 바꾼다."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain(v) for v in value]
    return value


def first_token(name: str) -> str:
    return name.strip().lower().split()[0] if name.strip() else ""


def step_errors(state: dict, target: dict, steps: list[dict]) -> list[str]:
    """실행 단계를 보유 수량·체인·거래소·슬리피지로 검증한다 (추천 검증과 execute_order tool이 함께 쓴다).

    target은 후보 또는 추천 카드이며 chain, protocol 키를 쓴다.
    """
    held = {(h["asset_symbol"].upper(), h["chain"].lower()): h["amount"] for h in state["holdings"]}
    accounts = {first_token(e["name"]): {c.strip() for c in e["chains"].split(",")} for e in state["exchanges"]}
    if not steps:
        return ["실행 단계가 없습니다"]
    errors = []
    first = steps[0]
    key = (first["from_asset"].upper(), first["chain"].lower())
    # 입력 자산이 여러 단계에 나뉘어 들어가면(LP: 스왑분 + 예치분) 합계로 본다
    total = sum(s["amount"] for s in steps if (s["from_asset"].upper(), s["chain"].lower()) == key)
    if key not in held:
        errors.append(f"{key[0]}({key[1]})를 보유하고 있지 않습니다")
    elif first["amount"] <= 0 or total > held[key]:
        errors.append(f"{key[0]} 사용량 합계 {total:g}이 보유 수량 {held[key]:g}을 넘거나 0 이하입니다")
    for s in steps:
        chain = s["chain"].lower()
        venue = first_token(s["exchange"])
        if chain != target["chain"]:
            errors.append(f"단계 체인 {chain}이 대상 체인 {target['chain']}과 다릅니다")
        direct = venue == first_token(target["protocol"])
        via_account = venue in accounts and chain in accounts[venue]
        if not (direct or via_account):
            errors.append(f"'{s['exchange']}'은 사용자 계정이 없거나 {chain}을 지원하지 않습니다")
        if not 0 <= s["slippage_bps"] <= MAX_SLIPPAGE_BPS:
            errors.append(f"슬리피지 {s['slippage_bps']}bps가 한도 {MAX_SLIPPAGE_BPS}bps를 넘습니다")
    if target.get("action_type") == "lp":
        actions = [s["action"] for s in steps]
        if actions != ["swap", "deposit"]:
            errors.append(f"LP는 swap → deposit 두 단계여야 합니다 (현재 {actions})")
        elif abs(steps[0]["amount"] - steps[1]["amount"]) > 0.01 * max(steps[0]["amount"], steps[1]["amount"]):
            errors.append(f"LP는 swap 수량과 deposit 수량이 같아야 합니다 "
                          f"(현재 {steps[0]['amount']:g} / {steps[1]['amount']:g}, 각각 총액의 절반)")
    return errors


# ---- 프롬프트 구성 ----

def context_prompt(state: dict) -> str:
    p = state["profile"]
    holdings = "\n".join(
        f"- {h['asset_symbol']} ({h['chain']}) {h['amount']:g}개 ≈ ${h['usd_value']:,.0f}" for h in state["holdings"])
    exchanges = "\n".join(f"- {e['name']} ({e['type']}): {e['chains']}" for e in state["exchanges"])
    memories = "\n".join(
        f"M{i} [{m['memory_type']}] {m['content']}" for i, m in enumerate(state["memories"], 1)) or "(없음)"
    return (f"사용자: {p['name']} / 투자 성향 {p['risk_profile']} / 리스크 한도 {p['max_risk_level']}\n\n"
            f"보유 자산\n{holdings}\n\n거래소 계정 (지원 체인)\n{exchanges}\n\n장기 기억 (mem9)\n{memories}")


def candidates_prompt(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        apy = f"{c['expected_apy']:g}%" if c["expected_apy"] is not None else "미상"
        lines.append(
            f"#{i} {c['title']} | {c['action_type']} | {c['asset']}/{c['chain']} | {c['protocol']} "
            f"(감사 {'O' if c['audited'] else 'X'}) | 예상 수익 {apy} | 리스크 {c['risk_level']} | 기간 {c['horizon']} "
            f"| 보유 {c['holding_amount']:g} {c['asset']}\n   요약: {c['summary']}")
    return "\n".join(lines)


# ---- 노드 ----

def recall_memory(state: dict) -> dict:
    db = get_db()
    uid = state["user_id"]
    profile = db.query("SELECT id, name, risk_profile, max_risk_level, base_currency FROM users WHERE id = %s",
                       (uid,), label="추천: 사용자 프로필")[0]
    holdings = db.query(
        "SELECT asset_symbol, chain, amount, usd_value FROM user_holdings WHERE user_id = %s ORDER BY usd_value DESC",
        (uid,), label="추천: 보유 자산")
    exchanges = db.query(
        """
        SELECT e.name, e.type, GROUP_CONCAT(ec.chain ORDER BY ec.chain SEPARATOR ', ') AS chains
        FROM user_exchange_accounts ua
        JOIN exchanges e        ON e.id = ua.exchange_id
        JOIN exchange_chains ec ON ec.exchange_id = e.id
        WHERE ua.user_id = %s AND ua.status = 'ACTIVE'
        GROUP BY e.id, e.name, e.type
        """, (uid,), label="추천: 거래소 계정")
    # 직전 승인·거절의 기억 정리가 뒤에서 진행 중이면 끝나기를 기다린다 (2회차가 새 기억을 반영하도록)
    memory_jobs.wait_pending(user_app_id(uid, state["workspace"]), timeout=INSIGHT_WAIT_SECONDS + 10)
    # q 검색은 단어 일치 방식이라 쓰지 않고 전체 목록을 가져온다 (ARCHITECTURE.md 1.2)
    memories = [m for m in get_mem9().list_memories(user_app_id(uid, state["workspace"]))
                if m["memory_type"] in ("pinned", "insight")]
    return plain({
        "profile": profile,
        "holdings": holdings,
        "exchanges": exchanges,
        "memories": [{k: m.get(k) for k in ("id", "content", "memory_type", "tags", "created_at")}
                     for m in memories],
    })


def build_intent(state: dict) -> dict:
    out: SearchIntent = structured(SearchIntent, effort="low").invoke(
        [SystemMessage(INTENT_SYSTEM + output_language_rule(state.get("lang", "ko"))), HumanMessage(context_prompt(state))])
    return {"intent": out.intent, "intent_reason": out.reason}


def search_candidates(state: dict) -> dict:
    rows = get_db().query(HYBRID_SQL, {"ws": state["workspace"], "uid": state["user_id"], "intent": state["intent"]},
                          label="추천: 하이브리드 검색 (벡터 + 관계형)")
    return {"candidates": plain(rows)}


def recommend(state: dict) -> dict:
    attempts = state.get("attempts", 0) + 1
    if not state["candidates"]:
        return {"drafts": [], "attempts": attempts}
    prompt = f"{context_prompt(state)}\n\n후보 (하이브리드 검색 결과)\n{candidates_prompt(state['candidates'])}"
    if errors := state.get("validation_errors"):
        prompt += "\n\n이전 응답에서 다음 문제가 발견되었습니다. 고쳐서 다시 답하세요.\n" + "\n".join(f"- {e}" for e in errors)
    # thinking 요약을 스트리밍으로 받아 화면의 "AI 검토 메모"에 보여준다 (본문 JSON보다 몇 초 먼저 도착)
    out: RecommendationSet = structured(
        RecommendationSet, effort="low", thinking={"type": "adaptive", "display": "summarized"},
    ).invoke([SystemMessage(RECOMMEND_SYSTEM + output_language_rule(state.get("lang", "ko"))), HumanMessage(prompt)])
    return {"drafts": [c.model_dump() for c in out.cards], "attempts": attempts}


def validate(state: dict) -> dict:
    """LLM이 만든 카드를 보유 수량·거래소·체인·슬리피지로 다시 검증하고, 실제 ID로 바꾼다."""
    candidates, memories = state["candidates"], state["memories"]
    errors, cards, used = [], [], set()
    for d in sorted(state.get("drafts", []), key=lambda d: d["rank"]):
        no = d["candidate_no"]
        label = f"카드 '{d['headline']}'"
        if not 1 <= no <= len(candidates):
            errors.append(f"{label}: 후보 #{no}는 목록에 없습니다")
            continue
        if no in used:
            errors.append(f"{label}: 후보 #{no}를 중복으로 골랐습니다")
            continue
        cand = candidates[no - 1]
        steps = d["command"]["steps"]
        if card_errors := step_errors(state, cand, steps):
            errors.extend(f"{label}: {e}" for e in card_errors)
            continue

        used.add(no)
        cards.append({
            "rank": len(cards) + 1,
            "headline": d["headline"],
            "rationale": d["rationale"],
            "opportunity_id": cand["id"],
            **{k: cand[k] for k in ("title", "protocol", "chain", "asset", "action_type",
                                    "expected_apy", "risk_level", "distance", "strategy_id")},
            "memory_refs": [memories[n - 1] for n in d["memory_nos"] if 1 <= n <= len(memories)],
            # 제안 명령: 실행 agent가 execute_order tool을 부를 때 출발점으로 쓴다
            "proposed_steps": steps,
        })
        if len(cards) == MAX_CARDS:
            break
    return {"cards": cards, "validation_errors": errors}


def route_after_validate(state: dict) -> str:
    if state["validation_errors"] and state["attempts"] < MAX_ATTEMPTS:
        return "recommend"
    return "save_recommendations" if state["cards"] else "no_result"


def no_result(state: dict) -> dict:
    reason = "조건에 맞는 후보가 없습니다" if not state["candidates"] else "검증을 통과한 추천이 없습니다"
    return {"error": reason}


def save_recommendations(state: dict) -> dict:
    db = get_db()
    model = get_settings().llm_model
    cards = []
    for c in state["cards"]:
        rec_id = db.execute(
            """
            INSERT INTO recommendations
              (workspace, user_id, opportunity_id, thread_id, `rank`, rationale, memory_refs, command, model)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (state["workspace"], state["user_id"], c["opportunity_id"], state["thread_id"], c["rank"], c["rationale"],
             json.dumps([m["id"] for m in c["memory_refs"]]),
             json.dumps({"proposed_steps": c["proposed_steps"]}, ensure_ascii=False), model),
            label="추천: 추천 저장 (AWAITING_APPROVAL)")
        cards.append({**c, "recommendation_id": rec_id})
    return {"cards": cards}


# ---- 실행할 추천 선택 ----

def choose(state: dict) -> dict:
    # interrupt가 맨 앞에 있으므로, 재개 시 아래 DB 쓰기는 한 번만 실행된다
    pick = interrupt({"type": "choose_card", "thread_id": state["thread_id"], "cards": state["cards"]})
    db = get_db()
    if pick.get("action") == "dismiss":
        db.execute("UPDATE recommendations SET status = 'SKIPPED', decided_at = NOW() WHERE thread_id = %s",
                   (state["thread_id"],), label="선택: 추천 닫기")
        return {"selected": None}
    card = next(c for c in state["cards"] if c["recommendation_id"] == pick["recommendation_id"])
    db.execute(
        """UPDATE recommendations SET status = 'SKIPPED', decided_at = NOW()
           WHERE thread_id = %s AND id <> %s AND status = 'AWAITING_APPROVAL'""",
        (state["thread_id"], card["recommendation_id"]), label="선택: 나머지 추천 정리")
    return {
        "selected": card,
        "selected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "messages": [HumanMessage(selected_card_prompt(state, card))],
    }


def route_after_choose(state: dict) -> str:
    return "exec_agent" if state.get("selected") else "done"


def selected_card_prompt(state: dict, card: dict) -> str:
    holdings = "\n".join(f"- {h['asset_symbol']} ({h['chain']}) {h['amount']:g}개" for h in state["holdings"])
    exchanges = "\n".join(f"- {e['name']}: {e['chains']}" for e in state["exchanges"])
    apy = f"{card['expected_apy']:g}%" if card["expected_apy"] is not None else "미상"
    return (f"이 추천을 실행해 주세요.\n\n"
            f"추천: {card['title']} ({card['protocol']}, {card['chain']}, {card['action_type']}, "
            f"예상 연 {apy}, 리스크 {card['risk_level']})\n근거: {card['rationale']}\n\n"
            f"제안 실행 단계\n{json.dumps(card['proposed_steps'], ensure_ascii=False, indent=1)}\n\n"
            f"보유 자산\n{holdings}\n\n거래소 계정 (지원 체인)\n{exchanges}")


# ---- 실행 agent ----

EXEC_SYSTEM = """당신은 주문 실행 도우미입니다. 사용자가 고른 추천을 실행 엔진에 주문으로 제출합니다.

- execute_order 도구로 주문을 제출합니다. 도구는 제출 전에 반드시 사용자의 승인을 받습니다.
- steps는 제안 실행 단계를 그대로 쓰는 것이 기본입니다. 보유 수량·위치·거래소가 맞지 않으면 고쳐서 넣습니다.
- chain은 자산이 있는 위치입니다. 거래소 계정(binance 등)이면 엔진이 거래소 API로, 네트워크(arbitrum 등)면 온체인으로 실행합니다.
- lp는 swap과 deposit 두 단계이며, 두 단계 amount는 같습니다 (각각 넣을 총액의 절반, 합계가 총액). note에도 총액과 나뉜 수량을 이 규칙대로 씁니다.
- note에는 승인 화면에 보일 주문 요약을 출력 언어로 한 문장 씁니다. 예: "USDC 2,000개를 Aave v3 아비트럼에 예치합니다."
- 도구가 검증 오류를 돌려주면 오류를 고쳐 다시 호출합니다.
- 사용자가 거절하면 다시 호출하지 말고, 거절 사유를 기억해 다음 추천에 반영하겠다고 한두 문장으로 알린 뒤 끝냅니다.
- 체결되면 결과를 한두 문장으로 요약하고 끝냅니다.
- 사용자가 승인·거절 화면에서 직접 입력한 이유는 앱이 사용자의 장기 기억(mem9)에 정리하는 정상 동작입니다. 저장 여부를 되묻거나 문제 삼지 말고, 이유가 있었다면 다음 추천에 반영하겠다고만 짧게 알립니다."""


def remember_decision(job: memory_jobs.Job, state: dict, card: dict, approve: bool, reason: str,
                      order: dict | None = None) -> None:
    """결정을 mem9에 기록한다 (memory_jobs 백그라운드 작업). 단계는 job.step으로 남겨 화면 타임라인에 보인다.

    순서: 이유 ingest → insight 대기(또는 Claude 대체 추출) → 결정 기록(pinned) → 체결 기록(승인 시).
    결정 기록에도 이유가 들어가므로, 먼저 저장하면 mem9가 같은 내용으로 보고 추출을 건너뛸 수 있다.
    """
    mem9 = get_mem9()
    app_id = user_app_id(state["user_id"], state["workspace"])
    lang = state.get("lang", "ko")
    if reason:
        # 사용자 발화만 보낸다: 한국어 추천 문장을 함께 보내면 추출이 일어나지 않았다 (ARCHITECTURE.md 1.2)
        known = {m["id"] for m in mem9.list_memories(app_id, memory_type="insight")}
        start = time.perf_counter()
        status = mem9.ingest(app_id, [{"role": "user", "content": reason}])
        job.step("sent", status=status, ms=round((time.perf_counter() - start) * 1000))
        job.step("extracting", state="running", polls=0)
        start = time.monotonic()
        insights = mem9.wait_for_insights(app_id, known, timeout=INSIGHT_WAIT_SECONDS,
                                          on_poll=lambda n: job.step("extracting", state="running", polls=n))
        if insights:
            job.step("extracting", secs=round(time.monotonic() - start, 1))
            job.events += [{"kind": "insight", "content": m["content"], "id": m["id"]} for m in insights]
            job.step("learned", items=[m["content"] for m in insights])
        else:
            job.step("extracting", state="warn", secs=INSIGHT_WAIT_SECONDS)
            facts: ExtractedFacts = structured(ExtractedFacts, effort="low").invoke(
                [SystemMessage(FACTS_SYSTEM + output_language_rule(lang)),
                 HumanMessage(f"추천: {card['title']}\n{'승인 이유' if approve else '거절 사유'}: {reason}")])
            for fact in facts.facts[:3]:
                m = mem9.add_pinned(app_id, fact, tags=["insight-fallback"])
                job.events.append({"kind": "fallback", "content": fact, "id": m.get("id")})
            job.step("fallback", items=facts.facts[:3])

    if lang == "en":
        verb = "Approved" if approve else "Rejected"
        text = f"{date.today().isoformat()} {verb} recommendation: {card['title']}" + (
            f" (reason: {reason})" if reason else "")
    else:
        verb = "승인" if approve else "거절"
        label = "이유" if approve else "사유"
        text = f"{date.today().isoformat()} {card['title']} 추천을 {verb}함" + (f" ({label}: {reason})" if reason else "")
    saved = mem9.add_pinned(app_id, text, tags=["decision"],
                            metadata={"recommendation_id": str(card["recommendation_id"])})
    job.events.append({"kind": "decision", "content": text, "id": saved.get("id")})
    job.step("decision_saved", text=text)

    if order:
        first = order["first_step"]
        text = (f"{date.today().isoformat()} Executed {card['title']}: {first['amount']:g} {first['from_asset']} "
                f"({first['chain']})" if lang == "en" else
                f"{date.today().isoformat()} {card['title']} 실행 완료: {first['amount']:g} {first['from_asset']} "
                f"({first['chain']})")
        saved = mem9.add_pinned(app_id, text, tags=["execution"], metadata={"tx_hash": order["tx_hash"]})
        job.events.append({"kind": "execution", "content": text, "id": saved.get("id")})
        job.step("execution_saved", text=text)


@tool
def execute_order(
    steps: list[ExecutionStep],
    note: str,
    state: Annotated[dict, InjectedState],
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """선택된 추천을 실행 엔진(거래소 API 또는 온체인)에 주문으로 제출합니다.

    제출 전에 보유 수량·체인·거래소·슬리피지를 검증하고, 엔진으로 결과를 시뮬레이션한 뒤
    사용자의 승인을 받습니다. 승인되면 엔진에 제출해 체결 결과를, 거절되면 거절 사유를 돌려줍니다.

    Args:
        steps: 실행 순서대로의 주문 단계.
        note: 승인 화면에 보일 주문 요약 (출력 언어로 한 문장).
    """
    # 재개하면 이 tool이 처음부터 다시 실행된다. 그래서 interrupt 앞에는 부수 효과 없는 검증·시뮬레이션만 두고,
    # 명령 ID와 만료 시각은 tool 호출 ID와 선택 시각에서 만들어 승인 화면에 보인 명령과 실행 명령을 같게 한다.
    card = state["selected"]
    calls = state.get("exec_tool_calls", 0) + 1
    steps = [s.model_dump() if hasattr(s, "model_dump") else dict(s) for s in steps]

    def reply(text: str, **update) -> Command:
        return Command(update={"messages": [ToolMessage(text, tool_call_id=tool_call_id)],
                               "exec_tool_calls": calls, **update})

    if errors := step_errors(state, card, steps):
        return reply("검증 실패. 다음을 고쳐 다시 호출하세요:\n" + "\n".join(f"- {e}" for e in errors))

    command = ExecutionCommand(
        command_id=f"cmd_{tool_call_id[-12:]}",
        user_id=state["user_id"],
        strategy_id=card["strategy_id"],
        steps=steps,
        max_slippage_bps=max(s["slippage_bps"] for s in steps),
        expires_at=(datetime.fromisoformat(state["selected_at"]) + COMMAND_TTL).isoformat(timespec="seconds"),
    ).model_dump()
    simulation = engine_mock.simulate(steps, card["expected_apy"], command_id=command["command_id"])
    db = get_db()
    # 해외 거래소 계정 안의 주문이면 해외 잔고 정보(신고 참고)를 승인 화면에 함께 보여준다 (읽기만 함)
    overseas = None
    if {s["chain"].lower() for s in steps} & overseas_locations(db):
        overseas = overseas_status(db, state["user_id"])

    decision = interrupt({
        "type": "order_approval",
        "recommendation_id": card["recommendation_id"],
        "title": card["title"],
        "note": note,
        "command": command,
        "simulation": simulation,
        "overseas": overseas,
    })

    approve = decision.get("action") == "approve"
    reason = (decision.get("reason") or "").strip()
    db.execute("UPDATE recommendations SET status = %s, command = %s, decided_at = NOW() WHERE id = %s",
               ("APPROVED" if approve else "REJECTED", json.dumps(command, ensure_ascii=False),
                card["recommendation_id"]), label="승인: 결정 기록")
    app_id = user_app_id(state["user_id"], state["workspace"])
    if not approve:
        job_id = memory_jobs.submit(app_id, lambda job: remember_decision(job, state, card, False, reason))
        return reply(f"사용자가 주문을 거절했습니다. 사용자가 직접 입력한 사유: {reason or '없음'}. "
                     "앱이 이 사유를 사용자의 장기 기억에 정리해 다음 추천에 반영합니다.",
                     outcome="rejected", command=command, memory_job=job_id)

    order = engine_mock.submit(db, card["recommendation_id"], command)
    job_id = memory_jobs.submit(app_id, lambda job: remember_decision(
        job, state, card, True, reason, order={**order, "first_step": steps[0]}))
    remembered = (f" 사용자가 승인 화면에서 직접 입력한 승인 이유: {reason}. 앱이 이 이유를 사용자의 장기 기억에 정리해 "
                  "다음 추천에 반영합니다." if reason else "")
    return reply(f"승인되어 체결되었습니다. 주문 {order['order_id']}, 상태 {order['status']}, "
                 f"tx {order['tx_hash'][:18]}….{remembered}",
                 outcome="approved", command=command, order=order, memory_job=job_id)

EXEC_TOOLS = [execute_order]


def exec_agent(state: dict) -> dict:
    llm = get_llm(effort="low").bind_tools(EXEC_TOOLS, parallel_tool_calls=False)
    return {"messages": [llm.invoke([SystemMessage(EXEC_SYSTEM + output_language_rule(state.get("lang", "ko"))), *state["messages"]])]}


def route_after_exec_agent(state: dict) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None) and not state.get("outcome") \
            and state.get("exec_tool_calls", 0) < MAX_TOOL_CALLS:
        return "tools"
    return "finish"


def finish(state: dict) -> dict:
    if state.get("selected") and not state.get("outcome"):
        get_db().execute("UPDATE recommendations SET status = 'FAILED', decided_at = NOW() WHERE id = %s",
                         (state["selected"]["recommendation_id"],), label="실행: 주문 실패")
        return {"outcome": "failed"}
    return {}
