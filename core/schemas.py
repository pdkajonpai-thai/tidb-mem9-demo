"""Pydantic models for LLM Structured Output (ARCHITECTURE.md section 5).

So the LLM does not make mistakes copying long IDs (19-digit AUTO_RANDOM, mem9 UUIDs), it picks candidates
and memories by the numbers attached in the prompt, and the app maps them to the real IDs.
Numeric ranges (1~5, 0~1) are validated and clamped in the app rather than by schema constraints.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ActionType = Literal["swap", "lend", "stake", "lp"]


# ---- Analysis graph ----

class Opportunity(BaseModel):
    title: str                     # max 25 Korean chars, e.g. "Aave v3 아비트럼 USDC 예치" (Aave v3 Arbitrum USDC deposit)
    asset: str                     # asset the user puts in, e.g. "USDC" (for LP, the stablecoin side)
    chain: str                     # e.g. "arbitrum"
    protocol: str                  # name from the supported protocol list, e.g. "Aave v3"
    action_type: ActionType
    expected_apy: float | None
    risk_level: int                # 1 (low) ~ 5 (high)
    horizon: Literal["short", "mid", "long"]
    confidence: float              # 0 ~ 1
    summary: str                   # 2~3 Korean sentences, used for embedding
    rationale: str


class SignalAnalysis(BaseModel):
    is_actionable: bool
    reason: str                    # reason when there is no opportunity (warning, commentary, etc.)
    opportunities: list[Opportunity]


# ---- Recommendation graph ----

class SearchIntent(BaseModel):
    intent: str                    # vector search query sentence (30~60 Korean characters)
    reason: str                    # why this sentence was made (shown in Behind the scenes)


class ExecutionStep(BaseModel):
    action: Literal["swap", "deposit", "stake", "withdraw"]
    exchange: str
    chain: str
    from_asset: str
    to_asset: str | None
    amount: float
    slippage_bps: int


class CommandDraft(BaseModel):
    steps: list[ExecutionStep]
    max_slippage_bps: int


class RecommendationCard(BaseModel):
    candidate_no: int              # candidate number in the prompt
    rank: int
    headline: str                  # one-line card title
    rationale: str                 # 2~4 Korean sentences, naturally mentioning the memories used
    memory_nos: list[int]          # memory numbers in the prompt (M1 → 1)
    command: CommandDraft


class RecommendationSet(BaseModel):
    cards: list[RecommendationCard]


class ExtractedFacts(BaseModel):
    facts: list[str]               # fallback path when mem9 extraction fails: 1~3 short Korean sentences


class Translations(BaseModel):
    items: list[str]               # Korean translations for display (same order and count as the input)


# ---- Execution engine contract ----

class ExecutionCommand(BaseModel):
    """기존 자동화 엔진에 넘어가는 계약. LLM 초안(CommandDraft)에 앱이 식별 정보를 채운다."""
    command_id: str
    user_id: int
    strategy_id: int
    steps: list[ExecutionStep]
    max_slippage_bps: int
    expires_at: str                # ISO 8601
    requires_approval: bool = True
