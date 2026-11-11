"""Explanatory content for Behind the scenes: LangGraph node table, technical notes tab (mem9 memory compression
and loss, LangGraph setup).

Sentences are kept as (Korean, English) pairs. Mermaid definitions contain only fixed diagrams built by code.
mem9 behavior follows the mem9 GitHub design docs (docs/architecture/mem9-algorithms) and this demo's observations
of the Hosted API.
"""
from __future__ import annotations

# Recommendation/approval graph nodes: (kind, what it does, state read, state written, next)
GRAPH_NODES: dict[str, dict[str, tuple[str, str]]] = {
    "recall_memory": {
        "kind": ("TiDB SQL + mem9", "TiDB SQL + mem9"),
        "does": ("프로필, 보유 자산, 거래소 계정을 SQL로 읽고, 같은 시간에 mem9 기억 전체를 읽습니다",
                 "Reads profile, holdings, and exchange accounts with SQL while loading all mem9 memories in parallel"),
        "reads": ("user_id, workspace", "user_id, workspace"),
        "writes": ("profile, holdings, exchanges, memories", "profile, holdings, exchanges, memories"),
        "next": ("build_intent", "build_intent"),
    },
    "build_intent": {
        "kind": ("LLM (Structured Output)", "LLM (Structured Output)"),
        "does": ("기억과 프로필로 벡터 검색에 쓸 검색 문장을 만듭니다", "Writes the vector search sentence from memories and profile"),
        "reads": ("profile, holdings, memories", "profile, holdings, memories"),
        "writes": ("intent, intent_reason", "intent, intent_reason"),
        "next": ("search_candidates", "search_candidates"),
    },
    "search_candidates": {
        "kind": ("TiDB SQL (하이브리드)", "TiDB SQL (hybrid)"),
        "does": ("벡터 유사도와 보유 자산, 거래소 체인, 리스크 한도를 한 SQL로 걸러 후보 10개를 고릅니다",
                 "Filters by vector similarity plus holdings, exchange chains, and risk limit in one SQL; returns 10 candidates"),
        "reads": ("intent, user_id", "intent, user_id"),
        "writes": ("candidates", "candidates"),
        "next": ("recommend", "recommend"),
    },
    "recommend": {
        "kind": ("LLM (thinking + Structured Output)", "LLM (thinking + Structured Output)"),
        "does": ("후보 중 최대 3개를 골라 근거와 실행 단계 초안을 씁니다", "Picks up to 3 candidates and drafts rationale and execution steps"),
        "reads": ("profile, memories, candidates, validation_errors", "profile, memories, candidates, validation_errors"),
        "writes": ("drafts, attempts", "drafts, attempts"),
        "next": ("validate", "validate"),
    },
    "validate": {
        "kind": ("코드 검증", "Code check"),
        "does": ("보유 수량, 체인, 거래소, 슬리피지를 코드로 다시 확인합니다", "Re-checks amounts, chains, exchanges, and slippage in code"),
        "reads": ("drafts, candidates, holdings, exchanges", "drafts, candidates, holdings, exchanges"),
        "writes": ("cards, validation_errors", "cards, validation_errors"),
        "next": ("조건: 오류가 있고 첫 시도면 recommend, 카드가 있으면 save_recommendations, 없으면 no_result",
                 "Conditional: recommend if errors on first try, save_recommendations if cards, otherwise no_result"),
    },
    "no_result": {
        "kind": ("코드", "Code"),
        "does": ("추천할 수 없는 이유를 남깁니다", "Records why there is no recommendation"),
        "reads": ("candidates", "candidates"),
        "writes": ("error", "error"),
        "next": ("끝", "End"),
    },
    "save_recommendations": {
        "kind": ("TiDB SQL", "TiDB SQL"),
        "does": ("카드를 AWAITING_APPROVAL 상태로 저장합니다", "Saves cards as AWAITING_APPROVAL"),
        "reads": ("cards", "cards"),
        "writes": ("cards (recommendation_id)", "cards (recommendation_id)"),
        "next": ("choose", "choose"),
    },
    "choose": {
        "kind": ("사람 대기 (interrupt)", "Human wait (interrupt)"),
        "does": ("사용자가 실행할 카드를 고를 때까지 멈춥니다", "Pauses until the user picks a card to execute"),
        "reads": ("cards", "cards"),
        "writes": ("selected, messages", "selected, messages"),
        "next": ("조건: 고르면 exec_agent, 닫으면 끝", "Conditional: exec_agent if picked, End if dismissed"),
    },
    "exec_agent": {
        "kind": ("LLM (tool 호출)", "LLM (tool calling)"),
        "does": ("execute_order tool의 인자(주문 단계, 요약)를 정하거나 결과를 요약합니다",
                 "Chooses execute_order arguments (steps, note) or summarizes the result"),
        "reads": ("messages", "messages"),
        "writes": ("messages", "messages"),
        "next": ("조건: tool 호출이 있으면 tools, 없으면 finish", "Conditional: tools if it called a tool, otherwise finish"),
    },
    "tools": {
        "kind": ("Tool (ToolNode) + 사람 대기", "Tool (ToolNode) + human wait"),
        "does": ("execute_order: 검증, 시뮬레이션, 주문 승인 대기, 엔진 제출, mem9 기억 정리 시작",
                 "execute_order: validate, simulate, wait for order approval, submit to engine, start mem9 memory job"),
        "reads": ("selected, holdings, exchanges", "selected, holdings, exchanges"),
        "writes": ("messages, outcome, command, order, memory_job", "messages, outcome, command, order, memory_job"),
        "next": ("exec_agent", "exec_agent"),
    },
    "finish": {
        "kind": ("TiDB SQL", "TiDB SQL"),
        "does": ("결과 없이 끝나면 추천을 FAILED로 표시합니다", "Marks the recommendation FAILED if it ended without an outcome"),
        "reads": ("selected, outcome", "selected, outcome"),
        "writes": ("outcome", "outcome"),
        "next": ("끝", "End"),
    },
}

VISITED_STYLE = "classDef visited fill:#0f766e,stroke:#0f766e,color:#ffffff;"

MEM9_FLOW = {
    "ko": """
flowchart LR
  C[대화 messages] --> X[1단계 사실 추출<br/>LLM, 사용자 발화 중심]
  X -- 일시적인 의도, 상태, 로그 --> D[버림]
  X --> R[2단계 조정 reconcile<br/>기존 insight, pinned와 비교]
  R -- ADD --> N[새 insight]
  R -- UPDATE --> U[옛 insight는 archived<br/>새 insight 생성]
  R -- DELETE --> Del[옛 insight는 deleted 상태]
  R -- NOOP --> Z[쓰지 않음]
  C -. 원문 .-> S[session 기록]
  P[pinned 직접 쓰기] --> K[그대로 저장<br/>조정이 바꾸지 않음]
""",
    "en": """
flowchart LR
  C[Conversation messages] --> X[Step 1 fact extraction<br/>LLM, mainly user turns]
  X -- transient intent, status, logs --> D[Dropped]
  X --> R[Step 2 reconcile<br/>compare with insight and pinned]
  R -- ADD --> N[New insight]
  R -- UPDATE --> U[Old insight archived<br/>new insight created]
  R -- DELETE --> Del[Old insight marked deleted]
  R -- NOOP --> Z[No write]
  C -. raw turns .-> S[session record]
  P[pinned direct write] --> K[Stored as is<br/>reconcile does not change it]
""",
}

MEM9_TEXT = {
    "ko": {
        "title": "#### mem9 장기 기억: 어떻게 줄여 저장하고, 무엇을 잃을 수 있나",
        "intro": ("mem9는 대화를 통째로 쌓지 않습니다. smart ingest는 대화에서 오래 쓸 사실만 골라 짧은 문장(insight)으로 "
                  "만들고, 이미 있는 기억과 맞춰 봅니다. 그래서 프롬프트에 넣을 기억이 작고 깔끔하게 유지됩니다. "
                  "대신 요약이므로 원문의 일부가 빠질 수 있습니다."),
        "how": (
            "**압축과 요약 방식**\n"
            "1. **사실 추출 (요약)**: 기본적으로 사용자 발화만 사실의 근거로 씁니다. 사실 하나는 혼자 읽어도 뜻이 통하는 "
            "짧은 문장이며, 어느 발화에서 나왔는지(source_seqs)를 함께 남깁니다. 검색 의도, 일시적인 상태, 작업 로그 같은 "
            "내용은 오래 쓸 사실이 아니라고 보고 버립니다.\n"
            "2. **조정 (중복과 충돌 정리)**: 새 사실마다 기존 insight와 pinned를 벡터와 키워드로 찾아, 후보(최대 60개)와 "
            "함께 LLM에 한 번에 넘깁니다. LLM은 ADD, UPDATE, DELETE, NOOP 중 하나를 고릅니다. UPDATE는 덮어쓰지 않고 "
            "옛 기억을 archived로 바꾼 뒤(superseded_by로 연결) 새 기억을 만듭니다. DELETE도 행을 지우지 않고 deleted "
            "상태로 바꿉니다.\n"
            "3. **pinned 보호**: 조정은 pinned를 지우거나 고치지 않습니다. pinned와 다른 내용이면 새 insight를 따로 만듭니다."),
        "loss_title": "**컨텍스트 손실 가능성과 이 데모의 대응**",
        "loss": [
            ("뉘앙스가 줄어듦", "추출은 요약이라 말투, 강도, 조건이 줄어들 수 있습니다. 설계 문서는 원문 언어를 유지하라고 하지만, "
             "이 데모의 Hosted 환경에서는 한국어 사유가 영어 insight로 저장되었습니다.",
             "사용자가 입력한 사유 원문을 결정 기록(pinned)에 그대로 함께 저장합니다."),
            ("추출되지 않음", "일시적인 내용이라고 판단하면 버립니다. LLM 오류가 나면 잘못된 쓰기를 막으려고 그 회차를 건너뜁니다.",
             "15초 안에 insight가 생기지 않으면 Claude로 사실을 뽑아 pinned로 저장합니다."),
            ("옛 사실이 바뀜", "UPDATE나 DELETE가 되면 옛 insight는 active 목록에서 빠집니다 (archived, deleted로 남음).",
             "결정과 체결 기록은 pinned라 조정 대상이 아닙니다. 추천 근거로 쓴 기억 ID는 TiDB recommendations.memory_refs에 남깁니다."),
            ("읽을 때 잘림", "목록 API는 한 번에 limit개만 돌려줍니다.",
             "응답의 total까지 offset으로 페이지를 넘겨 모두 읽습니다."),
            ("프롬프트가 길어짐", "기억이 수백 개로 늘면 모두 넣기 어렵습니다.",
             "데모 규모에서는 전체를 넣습니다. 실서비스에서는 mem9 하이브리드 검색으로 관련 기억만 고르고 pinned는 항상 넣는 방식을 권합니다."),
        ],
        "loss_cols": ("손실 가능성", "이유", "이 데모의 대응"),
        "where": ("**데모에서 확인하는 곳**: mem9 호출 탭(POST의 mode=smart 요청, GET 응답의 state, version 필드), "
                  "승인 화면의 'mem9 기억 정리' 패널, 대시보드의 Agent 기억"),
        "source": ("출처: mem9 GitHub 설계 문서 docs/architecture/mem9-algorithms (facts-extraction, reconciliation). "
                   "Hosted API의 동작(영어 저장, 처리 시간)은 이 데모에서 관찰한 결과입니다."),
    },
    "en": {
        "title": "#### mem9 long-term memory: how it compresses, and what can be lost",
        "intro": ("mem9 does not store whole conversations. Smart ingest picks only durable facts from the conversation, "
                  "writes them as short statements (insights), and reconciles them with existing memories. This keeps the "
                  "memory that goes into prompts small and clean. Because it is a summary, part of the original can be lost."),
        "how": (
            "**How it compresses and summarizes**\n"
            "1. **Fact extraction (summary)**: by default only user turns are used as evidence. Each fact is a short "
            "statement that makes sense on its own and records which turns it came from (source_seqs). Search intent, "
            "transient status, and activity logs are treated as not durable and dropped.\n"
            "2. **Reconcile (duplicates and conflicts)**: for each new fact, mem9 finds existing insight and pinned "
            "memories by vector and keyword search and sends them (up to 60 candidates) to an LLM in one call. The LLM "
            "chooses ADD, UPDATE, DELETE, or NOOP. UPDATE does not overwrite: the old memory becomes archived (linked by "
            "superseded_by) and a new one is created. DELETE also keeps the row and marks it deleted.\n"
            "3. **Pinned is protected**: reconcile never deletes or edits pinned memories. If a fact differs from a pinned "
            "memory, it creates a separate insight."),
        "loss_title": "**Possible context loss and how this demo handles it**",
        "loss": [
            ("Nuance is reduced", "Extraction is a summary, so tone, strength, and conditions can shrink. The design doc says "
             "to keep the original language, but on the Hosted API in this demo Korean reasons were stored as English insights.",
             "The user's original reason is also stored verbatim in the decision record (pinned)."),
            ("Nothing extracted", "Content judged transient is dropped. On an LLM error, the round is skipped to avoid wrong writes.",
             "If no insight appears within 15 seconds, Claude extracts the facts and saves them as pinned."),
            ("Old facts change", "After UPDATE or DELETE, the old insight leaves the active list (kept as archived or deleted).",
             "Decision and execution records are pinned, so reconcile does not touch them. Memory IDs used as evidence are "
             "kept in TiDB recommendations.memory_refs."),
            ("Cut off on read", "The list API returns only `limit` items per call.",
             "The demo pages through with offset until it reaches the response's total."),
            ("Prompt grows", "With hundreds of memories, putting all of them in is hard.",
             "At demo scale everything goes in. In production, pick relevant memories with mem9 hybrid search and always "
             "include pinned."),
        ],
        "loss_cols": ("Possible loss", "Why", "What this demo does"),
        "where": ("**Where to see it in the demo**: the mem9 calls tab (POST with mode=smart, the state and version fields in "
                  "GET responses), the 'mem9 memory update' panel on the approval screen, and Agent memory on the dashboard"),
        "source": ("Source: mem9 GitHub design docs docs/architecture/mem9-algorithms (facts-extraction, reconciliation). "
                   "Hosted API behavior (English storage, timing) is what this demo observed."),
    },
}

LANGGRAPH_FLOW = {
    "ko": """
sequenceDiagram
  participant UI as 화면 (Streamlit)
  participant G as LangGraph
  participant N as 노드
  participant DB as TiDB 체크포인트
  UI->>G: graph.stream(입력, thread_id)
  loop 노드마다
    G->>N: state 전달
    N-->>G: 바뀐 state만 돌려줌
    G->>DB: 체크포인트 저장
    G-->>UI: updates, messages 스트림
  end
  G-->>UI: interrupt (사람을 기다림)
  Note over UI,DB: 상태는 TiDB에 있어 새로고침이나 재배포 뒤에도 이어집니다
  UI->>G: Command(resume=선택 또는 승인)
  G->>DB: 체크포인트 읽기
  G->>N: 멈춘 노드부터 다시 실행
""",
    "en": """
sequenceDiagram
  participant UI as Screen (Streamlit)
  participant G as LangGraph
  participant N as Node
  participant DB as TiDB checkpoints
  UI->>G: graph.stream(input, thread_id)
  loop each node
    G->>N: pass state
    N-->>G: return only changed keys
    G->>DB: save checkpoint
    G-->>UI: updates and messages stream
  end
  G-->>UI: interrupt (wait for a person)
  Note over UI,DB: State lives in TiDB, so it survives a refresh or redeploy
  UI->>G: Command(resume=choice or approval)
  G->>DB: load checkpoint
  G->>N: rerun from the paused node
""",
}

LANGGRAPH_TEXT = {
    "ko": {
        "title": "#### LangGraph 한눈에: 구성 요소와 실행 흐름",
        "intro": ("LangGraph는 agent의 단계를 그래프로 정의하는 라이브러리입니다. 단계(노드)와 다음 단계(엣지)를 코드로 "
                  "고정하므로, LLM이 맡는 부분과 코드가 맡는 부분(검증, 승인, 저장)을 분명히 나눌 수 있습니다."),
        "cols": ("구성 요소", "뜻", "이 데모에서"),
        "rows": [
            ("State", "노드 사이에 주고받는 하나의 dict입니다. 노드는 바꾼 키만 돌려주고, reducer가 합칩니다.",
             "RecommendState. messages는 add_messages로 이어 붙이고, 분석 그래프의 drafts는 operator.add로 모읍니다."),
            ("Node", "state를 받아 일부를 고쳐 돌려주는 함수입니다.",
             "LLM 노드(build_intent, recommend, exec_agent), SQL 노드, 코드 검증 노드"),
            ("Edge", "다음에 실행할 노드입니다. 일반 엣지, 조건 엣지, Send가 있습니다.",
             "조건 엣지(그림의 점선): 검증 실패 시 재작성, tool 호출 여부. Send: 분석 그래프가 원문 40건을 동시에 분석"),
            ("Tool, ToolNode", "LLM이 인자를 정해 부르는 함수와, 그 호출을 실행하는 노드입니다.",
             "execute_order: 검증, 시뮬레이션, 주문 승인 대기, 엔진 제출"),
            ("interrupt, Command(resume)", "그래프를 멈추고 사람의 입력을 기다렸다가 그 값으로 이어 실행합니다.",
             "choose(실행할 추천 선택), execute_order 안(주문 승인)"),
            ("Checkpointer", "단계마다 state를 저장해 멈춘 곳에서 이어 갑니다.",
             "TiDBSaver: TiDB 테이블 3개. 체크포인트는 다음 노드와 동시에 저장(durability async)하고, 연결을 따로 씁니다."),
            ("RetryPolicy", "노드가 실패하면 다시 실행합니다.", "LLM 노드는 최대 3번"),
            ("Stream", "실행 중 결과를 바로 받습니다.",
             "updates(노드 완료: 진행 단계, 노드별 시간), messages(LLM 토큰: AI 검토 메모, 카드 작성, agent 요약)"),
        ],
        "where": ("**데모에서 확인하는 곳**: LangGraph 탭의 그래프 그림, 노드 설명표, 마지막 실행 경로와 노드별 시간, "
                  "체크포인트 이력"),
    },
    "en": {
        "title": "#### LangGraph at a glance: components and execution flow",
        "intro": ("LangGraph is a library for defining an agent's steps as a graph. Steps (nodes) and what comes next (edges) "
                  "are fixed in code, so the parts the LLM handles and the parts code handles (validation, approval, "
                  "storage) are clearly separated."),
        "cols": ("Component", "Meaning", "In this demo"),
        "rows": [
            ("State", "One dict passed between nodes. Nodes return only the keys they changed, and reducers merge them.",
             "RecommendState. messages are appended with add_messages; the analysis graph collects drafts with operator.add."),
            ("Node", "A function that takes state and returns part of it changed.",
             "LLM nodes (build_intent, recommend, exec_agent), SQL nodes, code check nodes"),
            ("Edge", "Which node runs next: normal edges, conditional edges, and Send.",
             "Conditional edges (dotted in the graph): rewrite on validation errors, whether a tool was called. Send: the "
             "analysis graph analyzes 40 signals at once"),
            ("Tool, ToolNode", "A function the LLM calls with arguments it chooses, and the node that runs the call.",
             "execute_order: validate, simulate, wait for order approval, submit to engine"),
            ("interrupt, Command(resume)", "Pauses the graph for human input, then continues with that value.",
             "choose (pick a recommendation), inside execute_order (order approval)"),
            ("Checkpointer", "Saves state at each step so the graph can continue where it paused.",
             "TiDBSaver: 3 TiDB tables. Checkpoints are written alongside the next node (durability async) on their own "
             "connection."),
            ("RetryPolicy", "Reruns a node when it fails.", "Up to 3 tries for LLM nodes"),
            ("Stream", "Delivers results while the graph runs.",
             "updates (node done: progress steps, time per node), messages (LLM tokens: AI review notes, card drafting, "
             "agent summary)"),
        ],
        "where": ("**Where to see it in the demo**: in the LangGraph tab, the graph diagram, the node table, the last run's "
                  "path and time per node, and the checkpoint history"),
    },
}
