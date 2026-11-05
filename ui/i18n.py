"""한·영 문구 사전과 언어 도구.

- 고정 문구: t("key", **fmt). 두 언어의 내용은 같다.
- 언어 값은 session_state["_lang"]에 두고, 사이드바·로그인 화면의 토글이 바꾸며 URL(?lang=English)에도 맞춘다.
- 저장된 내용·다른 언어로 만든 AI 결과는 ui.common.localized()가 화면에서 번역한다.
"""
from __future__ import annotations

import streamlit as st

LANGS = ("ko", "en")
# 토글 선택지 = URL 값. 공유 링크는 ?lang=English
OPTIONS = {"한국어": "ko", "English": "en"}
OPTION_OF = {v: k for k, v in OPTIONS.items()}
LANG_KEY = "_lang"          # 선택한 언어 (위젯이 아닌 값이라 페이지를 옮겨도 지워지지 않는다)


def default_lang() -> str:
    try:
        return "ko" if (st.context.locale or "").lower().startswith("ko") else "en"
    except Exception:
        return "ko"


def lang() -> str:
    # 토글이 그려지기 전(메뉴·사이드바 제목)에도 공유 링크의 ?lang= 값을 따르도록 URL을 함께 본다
    try:
        value = st.session_state.get(LANG_KEY) or st.query_params.get("lang")
    except Exception:
        value = None
    return OPTIONS.get(value) or default_lang()


def _keep_choice() -> None:
    st.session_state[LANG_KEY] = st.session_state["lang_choice"]


def lang_toggle() -> None:
    # bind="query-params"를 쓰면 URL의 lang을 지울 수 없어 st.switch_page가 실패하고,
    # 위젯 key에 둔 값은 st.switch_page로 페이지를 옮길 때 지워진다.
    # 그래서 언어는 위젯 밖(LANG_KEY)에 두고, 위젯은 매번 그 값으로 그린 뒤 URL에도 맞춘다.
    if st.session_state.get(LANG_KEY) not in OPTIONS:
        st.session_state[LANG_KEY] = OPTION_OF[lang()]
    st.session_state["lang_choice"] = st.session_state[LANG_KEY]
    st.segmented_control("언어 / Language", list(OPTIONS), required=True, key="lang_choice", on_change=_keep_choice)
    if st.query_params.get("lang") != st.session_state[LANG_KEY]:
        st.query_params["lang"] = st.session_state[LANG_KEY]


def t(key: str, **fmt) -> str:
    entry = T.get(key)
    if entry is None:
        return key
    text = entry[0] if lang() == "ko" else entry[1]
    return text.format(**fmt) if fmt else text


def krw(value: int) -> str:
    """원화 금액: 한국어 '4.60억 원', 영어 'KRW 460M'."""
    if lang() == "ko":
        return f"{value / 100_000_000:.2f}억 원"
    return f"KRW {value / 1_000_000:,.0f}M"


def label(text: str) -> str:
    """SQL 로그 라벨 표시용."""
    return text if lang() == "ko" else LABELS_EN.get(text, text)


def columns(rows: list[dict]) -> list[dict]:
    """영문 키로 만든 행을 현재 언어의 컬럼 이름으로 바꾼다."""
    return [{t(f"col.{k}"): v for k, v in row.items()} for row in rows]


# (한국어, English)
T: dict[str, tuple[str, str]] = {
    # 공통
    "app.title": ("TiDB + mem9 AI 투자 추천 데모", "TiDB + mem9 AI investment recommendation demo"),
    "app.sidebar_title": ("TiDB + mem9 데모", "TiDB + mem9 demo"),
    "disclaimer": ("정보 제공용 데모입니다. 투자 자문이나 세무 자문이 아니며, 실행은 시뮬레이션입니다. "
                   "시그널·인물·수익률·잔고는 합성 데이터입니다.",
                   "This demo is for information only. It is not investment or tax advice, and execution is "
                   "simulated. Signals, people, yields, and balances are synthetic data."),
    "err.env_missing": ("환경변수가 설정되지 않았습니다: {keys} — .env.example 참고",
                        "Missing environment variables: {keys} — see .env.example"),
    "err.auth_missing": ("로그인 설정([auth])이 없어 앱을 열 수 없습니다. 배포 환경에서는 Secrets에 [auth]를 넣고, "
                         "로컬 테스트에서만 DEMO_AUTH=off를 쓰세요.",
                         "The app can't open because sign-in ([auth]) isn't configured. Add [auth] to Secrets "
                         "when deployed; use DEMO_AUTH=off only for local testing."),
    "nav.signal": ("Signal 분석", "Signal analysis"),
    "nav.dashboard": ("사용자 대시보드", "User dashboard"),
    "nav.recommend": ("개인화 추천", "Personalized recommendations"),
    "nav.approval": ("승인 & 실행", "Approval & execution"),
    "nav.behind": ("Behind the scenes", "Behind the scenes"),
    "nav.audit": ("접속 기록", "Access log"),

    # 로그인
    "login.intro": ("AI가 투자 정보를 분석하고, 사용자의 자산·기억에 맞춰 추천하고, 사람이 승인하면 실행 엔진으로 "
                    "넘기는 과정을 TiDB와 mem9로 보여주는 데모입니다. LLM 비용이 드는 데모라 로그인이 필요합니다.",
                    "This demo uses TiDB and mem9 to show AI analyzing investment information, recommending "
                    "opportunities that fit each user's assets and memory, and handing approved orders to an "
                    "execution engine. Sign-in is required because the demo uses a paid LLM."),
    "login.button": ("Google로 로그인", "Sign in with Google"),
    "login.notice": ("로그인하면 이메일, 이름, 시각, 데모 사용 기록이 저장됩니다. "
                     "기록은 데모 운영과 LLM 비용 관리에만 씁니다.",
                     "When you sign in, your email, name, time, and demo activity are recorded. "
                     "The records are used only to run the demo and manage LLM cost."),
    "login.dev_mode": ("로컬 테스트 모드 (DEMO_AUTH=off) · {email}", "Local test mode (DEMO_AUTH=off) · {email}"),

    # 사이드바
    "side.persona": ("데모 사용자", "Demo persona"),
    "side.signed_in": ("로그인: {email}", "Signed in: {email}"),
    "side.logout": ("로그아웃", "Sign out"),
    "side.reset": ("데모 초기화", "Reset demo"),
    "side.reset_body": ("내 작업 공간의 분석 결과, 추천·실행 이력, 승인 대기, 사용자별 mem9 기억을 처음 상태로 되돌립니다. "
                        "Signal 분석부터 다시 시작합니다. **다른 방문자의 데이터에는 영향이 없습니다.**",
                        "Restores the analysis results, recommendation and execution history, pending approvals, "
                        "and each persona's mem9 memories in your workspace to the starting state. Start again "
                        "from Signal analysis. **Other visitors' data is not affected.**"),
    "side.reset_run": ("초기화 실행", "Run reset"),
    "side.reset_spinner": ("초기화 중… (약 15초)", "Resetting… (about 15 seconds)"),
    "side.reset_done": ("초기화했습니다.", "Reset complete."),
    "side.reset_spinner_all": ("전체 초기화 중… (약 20초)", "Resetting everything… (about 20 seconds)"),
    "p6.reset_all_title": ("**전체 초기화 (관리자)**", "**Reset everything (admin)**"),
    "p6.reset_all_body": ("모든 작업 공간({n}개)의 분석 결과, 추천·실행 이력, 승인 대기를 지우고 시드 데이터를 다시 넣습니다. "
                          "각 방문자의 mem9 기억은 다음에 접속할 때 처음 상태로 다시 기록됩니다. 접속 기록과 번역 캐시는 지우지 않습니다. "
                          "시드 데이터(data/)를 바꾼 뒤에 씁니다.",
                          "Clears the analysis results, recommendation and execution history, and pending approvals of "
                          "all workspaces ({n}) and reloads the seed data. Each visitor's mem9 memories are rewritten to "
                          "the starting state on their next visit. The access log and translation cache are kept. "
                          "Use this after changing the seed data (data/)."),
    "p6.reset_all_run": ("전체 초기화 실행", "Reset everything"),
    "side.reset_busy": ("다른 창에서 이미 초기화하고 있습니다. 잠시 뒤 다시 확인해 주세요.",
                        "A reset is already running in another window. Check again in a moment."),
    "side.workspace": ("작업 공간 `{ws}` · 데이터는 로그인한 계정마다 따로 저장됩니다",
                       "Workspace `{ws}` · data is kept separately for each signed-in account"),
    "ws.preparing": ("내 작업 공간을 준비하는 중… (처음 한 번, 약 10초)",
                     "Preparing your workspace… (first time only, about 10 seconds)"),

    # 용어
    "risk.conservative": ("보수형", "Conservative"),
    "risk.balanced": ("균형형", "Balanced"),
    "risk.aggressive": ("공격형", "Aggressive"),
    "action.lend": ("렌딩", "Lending"),
    "action.stake": ("스테이킹", "Staking"),
    "action.lp": ("유동성 공급", "Liquidity provision"),
    "action.swap": ("스왑", "Swap"),
    "status.AWAITING_APPROVAL": ("대기", "Waiting"),
    "status.APPROVED": ("승인", "Approved"),
    "status.REJECTED": ("거절", "Rejected"),
    "status.SKIPPED": ("선택 안 함", "Not selected"),
    "status.FAILED": ("실패", "Failed"),
    "route.exchange_api": ("거래소 API", "Exchange API"),
    "route.onchain": ("온체인", "On-chain"),
    # 이전 버전이 체크포인트에 남긴 경로 값 (승인 대기 중이던 주문 표시용)
    "route.거래소 API": ("거래소 API", "Exchange API"),
    "route.온체인": ("온체인", "On-chain"),
    "region.GLOBAL": ("해외 거래소", "Overseas exchange"),
    "region.KR": ("국내 거래소", "Korean exchange"),
    "region.ONCHAIN": ("온체인 지갑", "On-chain wallet"),
    "region_short.GLOBAL": ("해외", "Overseas"),
    "region_short.KR": ("국내", "Korea"),
    "region_short.ONCHAIN": ("온체인", "On-chain"),

    # 표 컬럼
    "col.signal": ("원문", "Signal"),
    "col.opportunity": ("기회", "Opportunity"),
    "col.asset": ("자산", "Asset"),
    "col.location": ("위치", "Location"),
    "col.protocol": ("프로토콜", "Protocol"),
    "col.type": ("유형", "Type"),
    "col.apy": ("예상 APY(%)", "Expected APY (%)"),
    "col.risk": ("리스크", "Risk"),
    "col.confidence": ("신뢰도", "Confidence"),
    "col.summary": ("요약 (자동 임베딩)", "Summary (auto-embedded)"),
    "col.reason": ("사유", "Reason"),
    "col.id": ("#", "#"),
    "col.source": ("출처", "Source"),
    "col.author": ("작성자", "Author"),
    "col.posted_at": ("시각", "Posted at"),
    "col.analyzed": ("분석", "Analyzed"),
    "col.content": ("내용", "Content"),
    "col.kind": ("구분", "Kind"),
    "col.node": ("노드", "Node"),
    "col.does": ("하는 일", "What it does"),
    "col.reads": ("읽는 state", "Reads"),
    "col.writes": ("쓰는 state", "Writes"),
    "col.next": ("다음", "Next"),
    "col.phase": ("구간", "Phase"),
    "col.ms": ("걸린 시간", "Time"),
    "col.amount": ("수량", "Amount"),
    "col.usd_value": ("평가액(USD)", "Value (USD)"),
    "col.exchange": ("거래소", "Exchange"),
    "col.reach": ("접근 위치", "Reachable locations"),
    "col.krw": ("원화 환산", "KRW value"),
    "col.distance": ("거리", "Distance"),
    "col.step": ("단계", "Step"),
    "col.action": ("동작", "Action"),
    "col.input": ("입력", "Input"),
    "col.output": ("출력", "Output"),
    "col.route": ("경로", "Route"),
    "col.slippage": ("슬리피지(bps)", "Slippage (bps)"),
    "col.decided_at": ("결정 시각", "Decided at"),
    "col.recommendation": ("추천", "Recommendation"),
    "col.decision": ("결정", "Decision"),
    "col.order": ("주문", "Order"),
    "col.tx": ("tx", "tx"),
    "col.at": ("시각", "Time"),
    "col.event": ("이벤트", "Event"),
    "col.email": ("이메일", "Email"),
    "col.name": ("이름", "Name"),
    "col.detail": ("상세", "Detail"),
    "col.last_seen": ("마지막 접속", "Last seen"),
    "col.logins": ("로그인", "Sign-ins"),
    "col.llm_actions": ("LLM 사용", "LLM actions"),

    # ① Signal 분석
    "p1.intro": ("인플루언서 게시물과 DeFi·거래소 공지 원문을 AI Agent가 읽고, 실행할 수 있는 투자 기회로 구조화합니다. "
                 "경고·논평처럼 기회가 없는 글은 걸러내고, 같은 기회를 다룬 글은 하나로 합칩니다.",
                 "An AI agent reads influencer posts and DeFi or exchange announcements and turns them into "
                 "actionable investment opportunities. Posts without an opportunity, such as warnings or "
                 "commentary, are filtered out, and posts about the same opportunity are merged."),
    "p1.metric_signals": ("원문", "Signals"),
    "p1.metric_todo": ("미분석", "Not analyzed"),
    "p1.count": ("{n}건", "{n}"),
    "p1.run": ("AI 분석 실행 ({n}건)", "Run AI analysis ({n})"),
    "p1.rerun": ("분석 결과 지우고 다시 하기", "Clear results and re-run"),
    "p1.progress_start": ("분석 준비 중…", "Preparing analysis…"),
    "p1.progress": ("원문 분석 {done}/{total} (동시 {c}건)", "Analyzing signals {done}/{total} ({c} at a time)"),
    "common.translating": ("현재 언어로 번역하는 중… (처음 한 번만, 이후에는 TiDB 번역 캐시 사용)",
                           "Translating into the current language… (first time only, then served from the TiDB cache)"),
    "p1.progress_translate": ("두 언어 화면용 번역을 준비하는 중…", "Preparing translations for both languages…"),
    "p1.progress_done": ("완료: 기회 {n}개 저장 ({s}초)", "Done: {n} opportunities saved ({s}s)"),
    "p1.tab_opps": ("투자 기회 {n}개", "Opportunities ({n})"),
    "p1.tab_raw": ("원문 {n}건", "Signals ({n})"),
    "p1.embed_caption": ("요약 컬럼은 INSERT할 때 TiDB가 EMBED_TEXT로 벡터를 자동 생성합니다. 별도 임베딩 API를 호출하지 않습니다.",
                         "TiDB generates the vector for the summary column with EMBED_TEXT on INSERT. "
                         "No separate embedding API is called."),
    "p1.empty": ("아직 분석하지 않았습니다. 위의 'AI 분석 실행'을 눌러 주세요.",
                 "Not analyzed yet. Select 'Run AI analysis' above."),
    "p1.skipped": ("제외된 원문 {n}건 (경고·논평·중복)", "Excluded signals: {n} (warnings, commentary, duplicates)"),
    "p1.raw_caption": ("원문은 수집한 그대로 보여줍니다 (한·영 혼합).", "Signals are shown as collected (Korean and English)."),

    # ② 대시보드
    "p2.risk_profile": ("투자 성향", "Risk profile"),
    "p2.risk_limit": ("리스크 한도", "Risk limit"),
    "p2.total": ("총 평가액", "Total value"),
    "p2.holdings": ("**보유 자산** · TiDB `user_holdings` (위치 = 온체인 네트워크 또는 거래소 계정)",
                    "**Holdings** · TiDB `user_holdings` (location = on-chain network or exchange account)"),
    "p2.accounts": ("**거래소 계정** · TiDB `user_exchange_accounts` + `exchange_chains`",
                    "**Exchange accounts** · TiDB `user_exchange_accounts` + `exchange_chains`"),
    "p2.memories": ("#### Agent 기억 {n}개\nmem9 `appId={app}`", "#### Agent memory ({n})\nmem9 `appId={app}`"),
    "mem.onboarding": ("**가입 시 선호** ({n})", "**Onboarding preferences** ({n})"),
    "mem.onboarding_desc": ("온보딩 때 저장한 선호입니다.", "Preferences saved at onboarding."),
    "mem.learned": ("**대화에서 배운 기억** ({n})", "**Learned from conversation** ({n})"),
    "mem.learned_desc": ("승인·거절할 때 남긴 이유에서 mem9가 추출했습니다.",
                         "Extracted by mem9 from the reasons given when approving or rejecting."),
    "mem.learned_empty": ("아직 없습니다. 승인 화면에서 승인하거나 거절할 때 이유를 입력하면 여기에 생깁니다.",
                          "None yet. Enter a reason when you approve or reject on the approval screen, and it "
                          "appears here."),
    "mem.history": ("**결정·실행 기록** ({n})", "**Decisions and executions** ({n})"),
    "p2.no_memories": ("아직 기억이 없습니다.", "No memories yet."),
    "p2.memory_legend": ("📌 직접 저장한 기억(pinned) · 💡 mem9가 대화에서 추출한 기억(insight). "
                         "mem9가 다른 언어로 추출한 기억은 화면에서만 현재 언어로 옮겨 보여줍니다.",
                         "📌 Saved directly (pinned) · 💡 Extracted by mem9 from conversation (insight). "
                         "Memories mem9 extracted in another language are translated for display only."),
    "p2.raw_memories": ("mem9 원문 그대로 보기", "Show mem9 originals"),
    "p2.overseas_title": ("**해외 거래소 월말 잔고** · TiDB `balance_snapshots` 월별 집계 (해외 계좌 신고 참고 정보)",
                          "**Overseas exchange month-end balances** · monthly aggregate of TiDB `balance_snapshots` "
                          "(reference for overseas account reporting)"),
    "p2.no_overseas": ("해외 거래소 잔고가 없습니다.", "No overseas exchange balances."),
    "p2.latest": ("최근 월말 ({m})", "Latest month-end ({m})"),
    "p2.peak": ("올해 최고 ({m})", "Highest this year ({m})"),
    "p2.gap": ("신고 기준 5억 원까지", "Left before the KRW 500M threshold"),
    "p2.exceeded_value": ("기준 초과", "Above threshold"),
    "p2.exceeded_title": ("올해 월말 합계가 5억 원을 넘은 달이 있습니다",
                          "A month-end total this year exceeded KRW 500M"),
    "p2.near_title": ("올해 최고 월말 잔고가 기준까지 {gap} 남았습니다",
                      "This year's highest month-end balance is {gap} below the threshold"),
    "p2.chart_y": ("억 원", "KRW 100M"),
    "p2.chart_rule": ("신고 기준 5억 원", "Reporting threshold KRW 500M"),
    "p2.chart_alt": ("해외 거래소 월말 잔고 합계 추이와 신고 기준 5억 원",
                     "Monthly overseas exchange balance totals against the KRW 500M reporting threshold"),
    "p2.by_exchange": ("최근 월말 거래소별 잔고", "Latest month-end balance by exchange"),
    "p2.synthetic": ("금액은 합성 가격·환율로 계산했습니다.", "Amounts use synthetic prices and exchange rates."),
    "overseas.notice": ("정보 제공용입니다. 신고 대상 여부와 신고 방법은 세무 전문가와 확인하세요.",
                        "For information only. Confirm whether and how to report with a tax professional."),

    # ③ 추천
    "p3.raw_response": ("mem9 원본 응답 보기 (JSON)", "Show raw mem9 response (JSON)"),
    "p3.raw_caption": ("GET /v1alpha2/mem9s/memories 응답 그대로입니다. 전체 {total}개 중 {n}개를 보여 줍니다. state, version은 "
                       "mem9가 기억을 조정할 때 쓰는 필드입니다.",
                       "The GET /v1alpha2/mem9s/memories response as returned, showing {n} of {total}. state and version are "
                       "the fields mem9 uses when it reconciles memories."),
    "p3.no_opps": ("투자 기회가 없습니다. 먼저 'Signal 분석'에서 AI 분석을 실행해 주세요.",
                   "There are no opportunities yet. Run the AI analysis on 'Signal analysis' first."),
    "p3.pending_order": ("승인을 기다리는 주문이 있습니다.", "An order is waiting for approval."),
    "p3.go_approval": ("승인 화면으로 이동", "Go to approval"),
    "p3.intro": ("**{name}** 님의 mem9 기억과 TiDB의 보유 자산·거래소 정보로 투자 기회를 추천합니다.",
                 "Recommends opportunities for **{name}** from their mem9 memories and the holdings and "
                 "exchange data in TiDB."),
    "p3.new": ("새 추천 받기", "Get new recommendations"),
    "p3.used_info": ("**이번 추천에 쓴 정보**", "**Inputs to these recommendations**"),
    "p3.intent": ("검색 의도 문장 (TiDB 벡터 검색 질의)", "Search intent (TiDB vector search query)"),
    "p3.memories": ("참고한 mem9 기억 {n}개", "mem9 memories used ({n})"),
    "p3.candidates": ("하이브리드 검색 후보 {n}개", "Hybrid search candidates ({n})"),
    "p3.cards_header": ("**추천** · 실행할 추천에서 '실행 요청'을 누르면 실행 agent가 엔진 tool을 호출합니다.",
                        "**Recommendations** · Select 'Request execution' on one to have the execution agent "
                        "call the engine tool."),
    "p3.outcome": ("최근 추천 처리 결과: '{title}' {label}. '새 추천 받기'로 다시 추천받을 수 있습니다.",
                   "Latest result: '{title}' {label}. Select 'Get new recommendations' to start again."),
    "p3.outcome.approved": ("승인·체결", "approved and filled"),
    "p3.outcome.rejected": ("거절", "rejected"),
    "p3.outcome.failed": ("실패", "failed"),
    "p3.outcome.pending": ("진행 중", "in progress"),

    # 카드·스트리밍 (ui.common)
    "card.account": ("{loc} 계정", "{loc} account"),
    "card.apy": ("연 {apy}%", "{apy}% APY"),
    "card.apy_none": ("수익률 미상", "APY unknown"),
    "card.risk": ("리스크 {r}/5", "Risk {r}/5"),
    "card.memories": ("참고한 기억 {n}개", "Memories used ({n})"),
    "card.steps": ("제안 실행 단계", "Proposed steps"),
    "card.steps_alt": ("{title} 제안 실행 단계", "Proposed steps for {title}"),
    "card.request": ("실행 요청", "Request execution"),
    "step.recall_memory": ("mem9 기억과 사용자 정보 불러오기", "Load mem9 memories and user data"),
    "step.build_intent": ("기억으로 검색 의도 문장 만들기", "Write a search intent from memories"),
    "step.search_candidates": ("TiDB 하이브리드 검색 (벡터 + 보유 자산·거래소·리스크)",
                               "TiDB hybrid search (vector + holdings, exchanges, risk)"),
    "step.recommend": ("Claude가 추천 카드 작성", "Claude writes recommendation cards"),
    "step.validate": ("실행 단계 검증", "Validate execution steps"),
    "step.save_recommendations": ("추천 저장", "Save recommendations"),
    "step.exec_agent": ("실행 agent 판단", "Execution agent decides"),
    "step.tools": ("엔진 tool 실행", "Run engine tool"),
    "stream.intent": ("검색 의도: {x}", "Search intent: {x}"),
    "stream.candidates": ("후보 {n}개", "{n} candidates"),
    "stream.memo": ("AI 검토 메모 (모델 추론 요약, 영문): …{x}", "AI review notes (model reasoning summary): …{x}"),
    "stream.rejected": ("거절을 기록했습니다. 사유는 mem9 기억 정리에서 뒤이어 처리합니다.",
                        "Rejection recorded. The reason is processed next by mem9 memory organizing."),
    "status.building": ("추천을 만드는 중…", "Building recommendations…"),
    "status.built": ("추천 완료", "Recommendations ready"),
    "status.calling_tool": ("실행 agent가 엔진 tool을 호출하는 중…", "The execution agent is calling the engine tool…"),
    "status.waiting_approval": ("주문 승인 대기", "Waiting for order approval"),
    "status.executing": ("주문 실행 중…", "Executing order…"),
    "status.recording": ("거절을 기록하는 중…", "Recording the rejection…"),
    "status.done": ("완료", "Done"),

    # ④ 승인
    "p4.none": ("승인을 기다리는 주문이 없습니다. '개인화 추천'에서 추천에 '실행 요청'을 눌러 주세요.",
                "No orders are waiting for approval. Select 'Request execution' on a recommendation in "
                "'Personalized recommendations'."),
    "p4.note": ("**주문 요약** · {note}", "**Order summary** · {note}"),
    "p4.tool_caption": ("tool 안에서 검증과 엔진 시뮬레이션을 마친 뒤, 사람의 승인을 기다리며 멈춰 있습니다 "
                        "(LangGraph interrupt). 체크포인트가 TiDB에 있어 새로고침·재접속해도 이 화면이 유지됩니다.",
                        "Inside the tool, validation and the engine simulation are done, and it is paused waiting "
                        "for human approval (LangGraph interrupt). The checkpoint is in TiDB, so this screen "
                        "survives a refresh or reconnect."),
    "p4.route": ("실행 경로", "Route"),
    "p4.position": ("투입 금액", "Amount committed"),
    "p4.fee": ("예상 네트워크 수수료", "Est. network fee"),
    "p4.yearly": ("연 예상 수익", "Est. yearly return"),
    "p4.overseas_title": ("해외 거래소 잔고 (해외 계좌 신고 참고 정보)", "Overseas exchange balance (reporting reference)"),
    "p4.overseas_body": ("이 주문은 해외 거래소 계정 안에서 실행되어 해외 거래소 잔고 합계는 그대로입니다. "
                         "최근 월말 {latest}, 올해 최고 {peak} ({gap}). {notice}",
                         "This order runs inside an overseas exchange account, so the overseas balance total "
                         "does not change. Latest month-end {latest}, highest this year {peak} ({gap}). {notice}"),
    "p4.gap": ("기준까지 {gap}", "{gap} below the threshold"),
    "p4.exceeded": ("이미 기준을 넘은 달이 있습니다", "a month has already exceeded the threshold"),
    "p4.steps_alt": ("주문 실행 단계", "Order execution steps"),
    "p4.command_caption": ("명령 ID `{id}` · 최대 슬리피지 {slip}bps · 만료 {exp}",
                           "Command ID `{id}` · max slippage {slip} bps · expires {exp}"),
    "p4.flow": ("AI가 이 주문을 엔진 명령으로 만든 과정 (Structured Output)",
                "How the AI turned this into an engine command (Structured Output)"),
    "p4.flow_intro": ("투자 정보가 스키마로 정해진 JSON을 거쳐 엔진 명령이 되기까지입니다. LLM 출력은 Pydantic 스키마로 "
                      "형식을 강제하고, 앱이 사용자 데이터로 검증한 뒤 식별 정보를 채워 기존 자동화 엔진의 API로 넘깁니다.",
                      "How investment information becomes an engine command through schema-defined JSON. LLM output "
                      "is forced into Pydantic schemas, the app validates it against the user's data, fills in the "
                      "identifiers, and hands it to the existing automation engine's API."),
    "p4.by_llm": ("LLM · Structured Output", "LLM · structured output"),
    "p4.by_app": ("앱", "App"),
    "p4.by_engine": ("엔진 API", "Engine API"),
    "p4.flow_1": ("원문 신호", "Source signal"),
    "p4.flow_1_caption": ("TiDB `raw_signals` #{id} · {source} · {author}", "TiDB `raw_signals` #{id} · {source} · {author}"),
    "p4.flow_2": ("투자 기회 JSON · `Opportunity` 스키마", "Opportunity JSON · `Opportunity` schema"),
    "p4.flow_2_caption": ("분석 그래프가 원문에서 추출해 TiDB `opportunities`에 저장했습니다. summary는 TiDB가 자동으로 임베딩합니다.",
                          "The analysis graph extracted this from the post and saved it to TiDB `opportunities`. "
                          "TiDB embeds the summary automatically."),
    "p4.flow_3": ("AI 주문 초안 · `CommandDraft` 스키마", "Order draft · `CommandDraft` schema"),
    "p4.flow_3_caption": ("추천 그래프가 사용자의 보유 자산·거래소 계정에 맞춰 만든 실행 단계입니다. 생성 {n}회 "
                          "(검증에 실패하면 오류를 알려 주고 다시 생성).",
                          "Execution steps the recommendation graph built for this user's holdings and exchange "
                          "accounts. Generated {n} time(s); if validation fails, the errors are sent back and it "
                          "regenerates."),
    "p4.flow_4": ("앱 검증", "App validation"),
    "p4.flow_4_caption": ("LLM이 만든 단계를 그대로 믿지 않고, TiDB의 사용자 데이터로 다시 확인합니다.",
                          "The app doesn't trust the LLM's steps as-is; it re-checks them against the user's data "
                          "in TiDB."),
    "p4.flow_checks": ("- ✓ 입력 자산을 보유하고 있고, 사용량 합계가 보유 수량 이하\n"
                       "- ✓ 모든 단계가 기회와 같은 위치(체인 또는 거래소 계정)\n"
                       "- ✓ 실행할 거래소·프로토콜에 사용자 계정이 있고 그 위치를 지원\n"
                       "- ✓ 슬리피지가 한도({max}bps) 이내",
                       "- ✓ The user holds the input asset, and the total used is within the holding\n"
                       "- ✓ Every step is at the opportunity's location (chain or exchange account)\n"
                       "- ✓ The user has an account on the exchange or protocol, and it supports that location\n"
                       "- ✓ Slippage is within the limit ({max} bps)"),
    "p4.flow_check_lp": ("- ✓ LP는 swap → deposit 두 단계이고 두 수량이 같음",
                         "- ✓ LP is two steps, swap → deposit, with equal amounts"),
    "p4.flow_5": ("실행 agent의 tool 호출 · `execute_order`", "Execution agent's tool call · `execute_order`"),
    "p4.flow_5_caption": ("실행 agent가 초안을 받아 tool 인자(JSON)로 호출했습니다. 호출 {n}회 "
                          "(tool이 검증 오류를 돌려주면 고쳐서 다시 호출).",
                          "The execution agent took the draft and called the tool with JSON arguments. Called {n} "
                          "time(s); if the tool returns validation errors, the agent fixes them and calls again."),
    "p4.flow_6": ("엔진 명령 · `ExecutionCommand`", "Engine command · `ExecutionCommand`"),
    "p4.flow_6_caption": ("앱이 명령 ID·사용자·전략·만료 시각·승인 필요 여부를 채운 최종 계약입니다. "
                          "승인하면 화면에 보이는 이 JSON이 그대로 엔진에 제출됩니다.",
                          "The final contract, with the command ID, user, strategy, expiry, and approval flag "
                          "filled in by the app. On approval, exactly this JSON is submitted to the engine."),
    "p4.flow_schema": ("엔진 계약 (JSON Schema) · 기존 엔진은 이 형식만 받으면 됩니다",
                       "Engine contract (JSON Schema) · the existing engine only needs to accept this format"),
    "p4.flow_7": ("엔진 시뮬레이션 응답", "Engine simulation response"),
    "p4.flow_7_caption": ("`POST /v1/orders/simulate` · 주문을 보내지 않고 예상 체결량·수수료·연 수익을 계산했습니다. "
                          "승인하면 `POST /v1/orders`로 제출됩니다.",
                          "`POST /v1/orders/simulate` · estimated fills, fees, and yearly return without placing "
                          "the order. On approval, it is submitted with `POST /v1/orders`."),
    "p4.engine_response": ("**엔진 응답** · `POST /v1/orders`", "**Engine response** · `POST /v1/orders`"),
    "p4.approve": ("승인하고 실행", "Approve and execute"),
    "p4.approve_reason": ("고른 이유 (선택, mem9 기억으로 남습니다)", "Why you chose this (optional, saved as a mem9 memory)"),
    "p4.approve_reason_ph": ("예: 언제든 인출할 수 있는 안정적인 이자가 좋아요",
                             "e.g. I like steady interest I can withdraw anytime"),
    "p4.reason": ("거절 사유 (mem9 기억으로 남습니다)", "Reason for rejecting (saved as a mem9 memory)"),
    "p4.reason_ph": ("예: 변동성이 큰 상품은 부담스러워요", "e.g. Volatile products make me uneasy"),
    "p4.reject": ("거절", "Reject"),
    "p4.just_done": ("**방금 처리한 주문**", "**Order just processed**"),
    "p4.filled": ("체결 완료 · 주문 {id} · {status} · tx `{tx}…`", "Filled · order {id} · {status} · tx `{tx}…`"),
    "job.title_running": ("mem9 기억 정리 · 진행 중 ({s}초)", "mem9 memory update · in progress ({s}s)"),
    "job.title_done": ("mem9 기억 정리 · 완료 · 새 기억 {n}개 ({s}초)", "mem9 memory update · done · {n} new ({s}s)"),
    "job.title_recorded": ("mem9 기억 정리 · 완료 ({s}초)", "mem9 memory update · done ({s}s)"),
    "job.title_failed": ("mem9 기억 정리 · 실패", "mem9 memory update · failed"),
    "job.intro": ("mem9가 이유에서 사실을 뽑아 장기 기억으로 정리합니다. 이 작업은 뒤에서 진행되며 화면을 막지 않습니다.",
                  "mem9 extracts facts from the reason and organizes them into long-term memory. This runs in the "
                  "background and does not block the screen."),
    "job.sent": ("이유를 mem9에 전송", "Sent the reason to mem9"),
    "job.sent_detail": ("POST /memories (mode=smart) · {status} · {ms}ms",
                        "POST /memories (mode=smart) · {status} · {ms}ms"),
    "job.extracting_running": ("mem9가 사실을 추출하는 중", "mem9 is extracting facts"),
    "job.extracting_done": ("mem9가 사실을 추출", "mem9 extracted facts"),
    "job.extracting_warn": ("mem9 추출이 {s}초 안에 끝나지 않음", "mem9 extraction did not finish within {s}s"),
    "job.extracting_detail": ("LLM 추출 → 기존 기억과 중복 정리 → 임베딩·저장 · 새 기억 확인 {n}회",
                              "LLM extraction → reconcile with existing memories → embed and store · "
                              "checked {n} times"),
    "job.secs": ("{s}초", "{s}s"),
    "job.learned": ("새 기억 {n}개", "New memories: {n}"),
    "job.fallback": ("Claude가 대신 추출해 저장", "Claude extracted and saved instead"),
    "job.decision_saved": ("결정 기록 저장", "Saved the decision"),
    "job.execution_saved": ("체결 기록 저장", "Saved the execution"),
    "job.to_dashboard": ("→ 대시보드 Agent 기억의 \"대화에서 배운 기억\"에 표시됩니다.",
                         "→ Shown under \"Learned from conversation\" in Agent memory on the dashboard."),
    "job.error": ("기억 정리 중 오류가 났습니다: {e}", "An error occurred while updating memory: {e}"),
    "mem.updating": ("정리 중…", "Updating…"),
    "p4.agent": ("**실행 agent**", "**Execution agent**"),
    "p4.history": ("**최근 결정 이력** · TiDB `recommendations` + `execution_orders`",
                   "**Recent decisions** · TiDB `recommendations` + `execution_orders`"),
    "p4.history_alt": ("최근 결정 이력", "Recent decisions"),

    # ⑤ Behind the scenes
    "p5.tab_sql": ("SQL Inspector", "SQL Inspector"),
    "p5.tab_mem9": ("mem9 호출", "mem9 calls"),
    "p5.tab_engine": ("엔진 호출", "Engine calls"),
    "p5.tab_graph": ("LangGraph", "LangGraph"),
    "p5.tab_explain": ("기술 설명", "Tech notes"),
    "p5.tab_arch": ("아키텍처", "Architecture"),
    "p5.sql_caption": ("내 작업 공간에서 TiDB에 보낸 SQL (최근 200개, 최신순). 벡터 검색과 관계형 조건이 한 SQL에 들어간 "
                       "'추천: 하이브리드 검색'이 핵심입니다. 체크포인터 내부 SQL은 포함하지 않습니다.",
                       "SQL sent to TiDB from your workspace (latest 200, newest first). The key query is "
                       "'Recommend: hybrid search', which combines vector search and relational filters in one "
                       "SQL. Checkpointer SQL is not included."),
    "p5.only_hybrid": ("하이브리드 검색만 보기", "Show hybrid search only"),
    "p5.rows": ("{n}행", "{n} rows"),
    "p5.mem9_caption": ("내 작업 공간의 mem9 REST API 호출 {n}건 (최신순). 추천할 때는 사용자 기억 전체를 목록으로 가져오고, "
                        "거절 사유는 smart ingest(messages + mode=smart)로 보냅니다.",
                        "{n} mem9 REST API calls from your workspace (newest first). Recommendations load the user's full memory "
                        "list, and rejection reasons are sent with smart ingest (messages + mode=smart)."),
    "p5.mem9_empty": ("아직 호출이 없습니다. 이 기록은 서버 메모리에 있어 앱이 재시작(배포)되면 비워집니다. "
                      "사용자 대시보드를 열거나 추천을 받으면 호출이 쌓입니다.",
                      "No calls yet. This log lives in server memory and is cleared when the app restarts "
                      "(for example, after a deploy). Open the user dashboard or get recommendations to add calls."),
    "p5.engine_caption": ("내 작업 공간에서 실행 agent의 tool이 엔진 API로 보낸 요청 {n}건 (최신순). 기존 자동화 엔진이 "
                          "들어갈 자리이며, 실제 연동할 때는 `core/engine_mock.py`만 바꿉니다.",
                          "{n} requests the execution agent's tool sent to the engine API from your workspace (newest "
                          "first). This is where the existing automation engine plugs in; a real integration only "
                          "replaces `core/engine_mock.py`."),
    "p5.engine_contract": ("**엔진 계약** · `ExecutionCommand` JSON Schema", "**Engine contract** · `ExecutionCommand` JSON Schema"),
    "p5.engine_empty": ("아직 호출이 없습니다. 추천에서 '실행 요청'을 누르면 시뮬레이션 요청이, 승인하면 주문 요청이 쌓입니다. "
                        "이 기록은 서버 메모리에 있어 앱이 재시작되면 비워집니다.",
                        "No calls yet. 'Request execution' on a recommendation adds a simulation request, and "
                        "approving adds an order request. This log lives in server memory and is cleared when the "
                        "app restarts."),
    "p5.engine_request": ("요청 본문", "Request body"),
    "p5.mem9_request": ("요청 (쿼리 파라미터, 본문)", "Request (query parameters, body)"),
    "p5.only_reads": ("기억 조회(GET)만 보기", "Show memory reads (GET) only"),
    "p5.no_body": ("응답 본문 없음", "No response body"),
    "p5.visited_caption": ("색칠된 노드: 최근 추천 스레드가 실제로 지나간 노드", "Filled nodes: nodes the latest recommendation thread actually ran"),
    "p5.nodes_title": ("**노드 설명** (실선은 항상 가는 엣지, 점선은 조건 엣지)",
                       "**Nodes** (solid lines always run next, dotted lines are conditional edges)"),
    "p5.timing_title": ("**최근 추천 스레드의 노드별 시간**", "**Time per node in the latest recommendation thread**"),
    "p5.timing_empty": ("이 스레드의 실행 기록이 없습니다. 추천을 새로 받으면 노드별 시간이 쌓입니다. 이 기록은 서버 메모리에 "
                        "있어 앱이 재시작되면 비워집니다.",
                        "No run record for this thread. Get a new recommendation to record time per node. This log lives "
                        "in server memory and is cleared when the app restarts."),
    "p5.timing_total": ("{phase} {s:.1f}초", "{phase} {s:.1f}s"),
    "p5.analysis_graph": ("**분석 그래프** (Send로 원문마다 analyze_signal을 동시에 실행)",
                          "**Analysis graph** (Send runs analyze_signal for each signal at once)"),
    "p5.analysis_graph_alt": ("원문 분석 LangGraph 그래프", "Signal analysis LangGraph graph"),
    "p5.mem9_flow_alt": ("mem9 smart ingest 흐름: 사실 추출, 조정, 저장", "mem9 smart ingest flow: extraction, reconcile, storage"),
    "p5.langgraph_flow_alt": ("LangGraph 실행 흐름: 스트림, 체크포인트, interrupt, 재개",
                              "LangGraph execution flow: stream, checkpoints, interrupt, resume"),
    "phase.recommend": ("추천", "Recommend"),
    "phase.execute": ("실행 요청", "Request execution"),
    "phase.decide": ("승인 또는 거절", "Approve or reject"),
    "p5.engine_response": ("응답", "Response"),
    "p5.graph_title": ("**추천·승인 그래프** · 점선은 조건 분기, `choose`와 `tools`(execute_order) 안에서 사람을 기다립니다",
                       "**Recommendation and approval graph** · dotted lines are conditional; it waits for a "
                       "person in `choose` and inside `tools` (execute_order)"),
    "p5.graph_alt": ("추천·승인 LangGraph 그래프", "Recommendation and approval LangGraph graph"),
    "p5.checkpoints": ("**체크포인트 이력** · 스레드 `{tid}` · TiDB `lg_checkpoints`",
                       "**Checkpoint history** · thread `{tid}` · TiDB `lg_checkpoints`"),
    "p5.checkpoints_alt": ("LangGraph 체크포인트 이력", "LangGraph checkpoint history"),
    "p5.state": ("현재 그래프 상태 (일부)", "Current graph state (partial)"),
    "p5.arch_alt": ("데모 아키텍처: 원문, 분석·추천 그래프, TiDB, mem9, 실행 agent, 실행 엔진",
                    "Demo architecture: signals, analysis and recommendation graphs, TiDB, mem9, execution "
                    "agent, execution engine"),
    "p5.arch_bullets": ("- **TiDB**: 업무 데이터, 벡터(Auto Embedding), LangGraph 체크포인트, 접속 기록을 한 DB에서 처리합니다.\n"
                        "- **mem9**: 사용자별 장기 기억입니다. 자체 저장소도 TiDB입니다.\n"
                        "- **실행 agent**: AI가 만든 주문을 JSON 명령(`ExecutionCommand`)으로 엔진 API에 넘깁니다. "
                        "승인은 tool 코드 안에서 강제하고, 엔진은 거래소 API 주문과 온체인 주문을 모두 받습니다.",
                        "- **TiDB**: business data, vectors (Auto Embedding), LangGraph checkpoints, and the "
                        "access log in one database.\n"
                        "- **mem9**: long-term memory per user. Its own storage is also TiDB.\n"
                        "- **Execution agent**: passes the AI-built order to the engine API as a JSON command "
                        "(`ExecutionCommand`). Approval is enforced inside the tool code, and the engine accepts "
                        "both exchange API orders and on-chain orders."),

    # ⑥ 접속 기록
    "p6.users": ("고유 사용자", "Unique users"),
    "p6.logins": ("로그인", "Sign-ins"),
    "p6.llm": ("LLM 사용", "LLM actions"),
    "p6.today": ("오늘 {n}", "Today {n}"),
    "p6.filter": ("이벤트 종류", "Event types"),
    "p6.recent": ("**최근 기록** (최신 500건)", "**Recent events** (latest 500)"),
    "p6.recent_alt": ("최근 접속·사용 기록", "Recent access and activity events"),
    "p6.per_user": ("**사용자별 집계**", "**By user**"),
    "p6.per_user_alt": ("사용자별 접속·사용 집계", "Access and activity by user"),
    "p6.download": ("CSV 다운로드", "Download CSV"),
    "p6.caption": ("접속 IP는 기록하지 않습니다. 이 호스팅 환경(Streamlit Community Cloud)은 실제 접속자 IP를 "
                   "앱에 전달하지 않아, 사용자 구분은 Google 계정으로 합니다.",
                   "IP addresses are not recorded. This hosting environment (Streamlit Community Cloud) does "
                   "not pass the real client IP to the app, so users are identified by their Google account."),
    "p6.not_admin": ("관리자만 볼 수 있습니다.", "Only admins can view this page."),
}

LABELS_EN = {
    "seed: drop": "seed: drop", "seed: insert": "seed: insert", "seed: reset": "seed: reset",
    "seed: schema": "seed: schema",
    "대시보드: 거래소 계정": "Dashboard: exchange accounts",
    "대시보드: 보유 자산": "Dashboard: holdings",
    "분석: 기회 저장 (summary 자동 임베딩)": "Analysis: save opportunities (summary auto-embedded)",
    "분석: 미분석 원문 조회": "Analysis: load unanalyzed signals",
    "분석: 원문 처리 표시": "Analysis: mark signals analyzed",
    "분석: 자산 목록": "Analysis: asset list",
    "분석: 초기화": "Analysis: reset",
    "분석: 프로토콜 목록": "Analysis: protocol list",
    "선택: 나머지 추천 정리": "Choose: close other recommendations",
    "선택: 추천 닫기": "Choose: dismiss recommendations",
    "승인: 결정 기록": "Approval: record decision",
    "승인: 결정 이력": "Approval: decision history",
    "실행: 엔진 전달 (SUBMITTED)": "Execution: sent to engine (SUBMITTED)",
    "실행: 주문 생성 (PENDING)": "Execution: create order (PENDING)",
    "실행: 주문 실패": "Execution: order failed",
    "실행: 체결 확인 (CONFIRMED)": "Execution: fill confirmed (CONFIRMED)",
    "추천: 거래소 계정": "Recommend: exchange accounts",
    "추천: 보유 자산": "Recommend: holdings",
    "추천: 사용자 프로필": "Recommend: user profile",
    "추천: 추천 저장 (AWAITING_APPROVAL)": "Recommend: save recommendations (AWAITING_APPROVAL)",
    "추천: 저장한 추천 ID": "Recommend: saved recommendation IDs",
    "추천: 하이브리드 검색 (벡터 + 관계형)": "Recommend: hybrid search (vector + relational)",
    "해외 잔고: 월별 합계": "Overseas balance: monthly totals",
    "해외 잔고: 최근 월말 거래소별": "Overseas balance: latest month-end by exchange",
    "해외 잔고: 해외 거래소 목록": "Overseas balance: overseas exchange list",
    "화면: 기회 수": "UI: opportunity count",
    "화면: 대기 중인 스레드": "UI: waiting threads",
    "화면: 사용자 목록": "UI: persona list",
    "화면: 원문 목록": "UI: signal list",
    "화면: 체크포인트 이력": "UI: checkpoint history",
    "화면: 최근 추천 스레드": "UI: latest recommendation thread",
    "화면: 투자 기회 목록": "UI: opportunity list",
    "화면: 번역 준비": "UI: prepare translations",
    "작업 공간: 확인": "Workspace: check", "작업 공간: 생성": "Workspace: create",
    "초기화: 주문": "Reset: orders", "초기화: 추천": "Reset: recommendations",
    "초기화: 체크포인트": "Reset: checkpoints", "초기화: 작업 공간": "Reset: workspace",
    "기록: 작업 공간 수": "Log: workspace count",
    "번역: 캐시 조회": "Translation: cache lookup", "번역: 캐시 저장": "Translation: cache save",
    "기록: 이벤트 저장": "Audit: save event",
    "기록: 이벤트 조회": "Audit: query events",
    "기록: 사용자별 집계": "Audit: aggregate by user",
    "기록: 요약 지표": "Audit: summary metrics",
}

ARCHITECTURE = {
    "ko": """
flowchart LR
  S[인플루언서·DeFi·거래소 원문] --> A[분석 그래프<br/>LangGraph + Claude]
  A --> T[(TiDB Cloud Starter<br/>관계형 + Auto Embedding 벡터<br/>+ LangGraph 체크포인트 + 접속 기록)]
  U[사용자] --> R[추천 그래프<br/>LangGraph + Claude]
  R <--> M[(mem9<br/>장기 기억 · TiDB 기반)]
  R <--> T
  R --> X[실행 agent<br/>execute_order tool]
  X -- 사람 승인 후 --> E[실행 엔진 Mock<br/>거래소 API · 온체인]
  X --> M
""",
    "en": """
flowchart LR
  S[Influencer, DeFi, and exchange posts] --> A[Analysis graph<br/>LangGraph + Claude]
  A --> T[(TiDB Cloud Starter<br/>relational + Auto Embedding vectors<br/>+ LangGraph checkpoints + access log)]
  U[User] --> R[Recommendation graph<br/>LangGraph + Claude]
  R <--> M[(mem9<br/>long-term memory · built on TiDB)]
  R <--> T
  R --> X[Execution agent<br/>execute_order tool]
  X -- after human approval --> E[Execution engine mock<br/>exchange API · on-chain]
  X --> M
""",
}
