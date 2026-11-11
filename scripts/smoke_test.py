"""Stage 1 smoke test: checks external service connections and items left unverified in the design.

    python scripts/smoke_test.py                 # all
    python scripts/smoke_test.py --only tidb,mem9

Checks
  tidb        TiDB connection (TLS) and version
  embed       Auto Embedding (EMBED_TEXT generated column + text similarity search)
  mem9        mem9 key status, pinned write/list, smart ingest insight extraction (PASS even if it fails; result shown)
  checkpoint  TiDB checkpointer (core/checkpoint_tidb.py): interrupt → resume on a new connection, history, delete
  llm_struct  ChatAnthropic native structured output
  llm_effort  ChatAnthropic effort passthrough
  llm_cache   ChatAnthropic prompt caching
  llm_stream  ChatAnthropic streaming

Test data (smoke_embed table, appId=smoke_* memories, smoke_* threads) is deleted at the end.
Checkpoint tables (lg_*) are kept because the app uses them too.
"""
from __future__ import annotations

import argparse
import json
import operator
import sys
import time
import traceback
from pathlib import Path
from typing import Annotated, TypedDict  # noqa: F401  (used when LangGraph resolves state type hints)
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import db  # noqa: E402
from core.config import missing  # noqa: E402


class CheckFailed(Exception):
    pass


def need(*names: str) -> None:
    if absent := missing(*names):
        raise CheckFailed(f".env에 값이 없습니다: {', '.join(absent)}")


TIDB_KEYS = ("TIDB_HOST", "TIDB_USER", "TIDB_PASSWORD")


def check_tidb() -> str:
    need(*TIDB_KEYS)
    conn = db.connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT VERSION(), DATABASE()")
            version, database = cur.fetchone()
            cur.execute("SHOW STATUS LIKE 'Ssl_cipher'")
            row = cur.fetchone()
    finally:
        conn.close()
    cipher = row[1] if row else "?"
    return f"{version} / DB={database} / TLS={cipher}"


def check_embed() -> str:
    need(*TIDB_KEYS)
    conn = db.connect(autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS smoke_embed")
            cur.execute(
                """
                CREATE TABLE smoke_embed (
                  id        INT PRIMARY KEY,
                  content   TEXT,
                  embedding VECTOR(1024) GENERATED ALWAYS AS (EMBED_TEXT(
                              "tidbcloud_free/cohere/embed-multilingual-v3",
                              content,
                              '{"input_type": "search_document", "input_type@search": "search_query"}'
                            )) STORED,
                  VECTOR INDEX ((VEC_COSINE_DISTANCE(embedding)))
                )
                """
            )
            cur.executemany(
                "INSERT INTO smoke_embed (id, content) VALUES (%s, %s)",
                [
                    (1, "스테이블코인 렌딩 수익률 상승, 원금 변동 위험 낮음"),
                    (2, "밈코인 레버리지 선물 롱 포지션, 하루 변동성 40%"),
                ],
            )
            cur.execute(
                """
                SELECT id, VEC_EMBED_COSINE_DISTANCE(embedding, %s) AS d
                FROM smoke_embed ORDER BY d LIMIT 2
                """,
                ("안정적인 이자 수익",),
            )
            rows = cur.fetchall()
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS smoke_embed")
        finally:
            conn.close()
    if not rows or rows[0][0] != 1:
        raise CheckFailed(f"유사도 순위가 예상과 다릅니다: {rows}")
    return f"'안정적인 이자 수익' → 1순위 id=1 (거리 {rows[0][1]:.3f} < {rows[1][1]:.3f})"


def check_mem9() -> str:
    from core.mem9_client import Mem9Client

    need("MEM9_API_KEY")
    app_id = f"smoke_{uuid4().hex[:8]}"
    client = Mem9Client()
    try:
        status = client.status()
        if status != "active":
            raise CheckFailed(f"키 상태가 active가 아닙니다: {status}")

        pinned = client.add_pinned(app_id, "레버리지 상품을 원하지 않음", tags=["smoke"])
        listed = client.list_memories(app_id)
        if pinned["id"] not in {m["id"] for m in listed}:
            raise CheckFailed(f"목록에 방금 쓴 기억이 없습니다: {listed}")

        # smart ingest shaped like the app's rejection record (user reason only) → wait for insight extraction.
        # Sending a Korean assistant message along with it prevented extraction (3 out of 3 tries).
        client.ingest(app_id, [
            {"role": "user", "content": "거절할게요. 변동성이 큰 상품은 부담스러워요. 비영구적 손실도 걱정돼요."},
        ])
        start = time.perf_counter()
        insights = client.wait_for_insights(app_id, set(), timeout=40)
        waited = time.perf_counter() - start
    finally:
        try:
            client.clear(app_id)
        finally:
            client.close()

    # extraction failure is not a FAIL because the app has a fallback path (Claude extraction → pinned)
    if insights:
        extracted = f"insight {len(insights)}개 추출 ({waited:.0f}초): " + " / ".join(
            m["content"] for m in insights)
    else:
        extracted = f"insight 추출 없음 ({waited:.0f}초 대기) → 앱에서는 대체 경로 사용"
    return f"status=active / pinned 쓰기·목록 OK / {extracted}"


def check_checkpoint() -> str:
    from langgraph.graph import END, START, StateGraph
    from langgraph.types import Command, interrupt

    from core.checkpoint_tidb import TiDBSaver

    need(*TIDB_KEYS)

    class State(TypedDict, total=False):
        log: Annotated[list[str], operator.add]
        cards: list[dict]
        decision: str

    def prepare(state: State) -> State:
        return {"log": ["prepare"], "cards": [{"rank": 1, "asset": "USDC", "apy": 4.2}]}

    def approval(state: State) -> State:
        return {"log": ["approval"], "decision": interrupt({"cards": state["cards"]})}

    def build():
        builder = StateGraph(State)
        builder.add_node("prepare", prepare)
        builder.add_node("approval", approval)
        builder.add_edge(START, "prepare")
        builder.add_edge("prepare", "approval")
        builder.add_edge("approval", END)
        database = db.Database()
        saver = TiDBSaver(database)
        saver.setup()
        return builder.compile(checkpointer=saver), saver, database

    thread_id = f"smoke_{uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id}}

    graph, saver, database = build()
    try:
        first = graph.invoke({}, config)
    finally:
        database.close()
    if "__interrupt__" not in first:
        raise CheckFailed(f"interrupt에서 멈추지 않았습니다: {first}")
    if first["__interrupt__"][0].value.get("cards") != first["cards"]:
        raise CheckFailed(f"interrupt 값이 상태와 다릅니다: {first['__interrupt__']}")

    # resume with a new connection, checkpointer, and graph → same conditions as a process restart
    graph, saver, database = build()
    try:
        resumed = graph.invoke(Command(resume="approve"), config)
        history = list(saver.list(config))
        saver.delete_thread(thread_id)
        leftover = saver.get_tuple(config)
    finally:
        database.close()
    if resumed.get("decision") != "approve" or resumed.get("log") != ["prepare", "approval"]:
        raise CheckFailed(f"재개 결과가 예상과 다릅니다: {resumed}")
    if leftover is not None:
        raise CheckFailed("delete_thread 후에도 체크포인트가 남아 있습니다")
    return f"interrupt → 새 연결로 resume 성공, 체크포인트 {len(history)}개 기록 후 삭제"


def check_llm_struct() -> str:
    from pydantic import BaseModel

    from core.llm import structured

    need("ANTHROPIC_API_KEY")

    class Capital(BaseModel):
        country: str
        city: str

    out = structured(Capital, include_raw=True).invoke("대한민국의 수도는?")
    if out.get("parsing_error") or not isinstance(out.get("parsed"), Capital):
        raise CheckFailed(f"파싱 실패: {out.get('parsing_error')}")
    mode = "도구 호출(tool use) 방식으로 대체됨" if out["raw"].tool_calls else "네이티브 json_schema"
    return f"{out['parsed']} / {mode}"


def check_llm_effort() -> str:
    from langchain_core.messages import HumanMessage

    from core.llm import get_llm

    need("ANTHROPIC_API_KEY")
    llm = get_llm(effort="low")
    payload = llm._get_request_payload([HumanMessage("ping")])  # private API: to inspect what is actually sent
    if "effort" not in json.dumps(payload):
        raise CheckFailed(f"요청 payload에 effort가 없습니다 (keys={sorted(payload)})")
    llm.invoke("한 단어로 답하세요: 하늘은 무슨 색?")
    where = "output_config" if "effort" in json.dumps(payload.get("output_config", {})) else "최상위"
    return f"payload {where}에 effort 포함, 호출 성공"


def check_llm_cache() -> str:
    from langchain_core.messages import HumanMessage, SystemMessage

    from core.llm import get_llm

    need("ANTHROPIC_API_KEY")
    # build a long system prompt to exceed the minimum cache length (new cache key each run)
    filler = f"[{uuid4().hex}] " + " ".join(
        f"Rule {i}: always answer briefly and politely." for i in range(800)
    )
    system = SystemMessage(
        content=[{"type": "text", "text": filler, "cache_control": {"type": "ephemeral"}}]
    )
    llm = get_llm(max_tokens=16)
    details = []
    for _ in range(2):
        res = llm.invoke([system, HumanMessage("ok?")])
        details.append((res.usage_metadata or {}).get("input_token_details", {}))
        time.sleep(1)
    if not details[1].get("cache_read"):
        raise CheckFailed(f"두 번째 호출에서 캐시 읽기가 없습니다: {details}")
    return f"1회차 cache_creation={details[0].get('cache_creation')}, 2회차 cache_read={details[1].get('cache_read')}"


def check_llm_stream() -> str:
    from core.llm import get_llm

    need("ANTHROPIC_API_KEY")
    chunks = [c for c in get_llm().stream("1부터 10까지 한 줄에 하나씩 쓰세요.") if c.content]
    if len(chunks) < 2:
        raise CheckFailed(f"청크가 {len(chunks)}개뿐입니다")
    return f"청크 {len(chunks)}개 수신"


CHECKS = {
    "tidb": check_tidb,
    "embed": check_embed,
    "mem9": check_mem9,
    "checkpoint": check_checkpoint,
    "llm_struct": check_llm_struct,
    "llm_effort": check_llm_effort,
    "llm_cache": check_llm_cache,
    "llm_stream": check_llm_stream,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", help=f"쉼표로 구분: {','.join(CHECKS)}")
    parser.add_argument("-v", "--verbose", action="store_true", help="실패 시 traceback 출력")
    args = parser.parse_args()

    names = args.only.split(",") if args.only else list(CHECKS)
    if unknown := [n for n in names if n not in CHECKS]:
        parser.error(f"알 수 없는 항목: {', '.join(unknown)}")

    failed = 0
    for name in names:
        start = time.perf_counter()
        try:
            detail = CHECKS[name]()
            mark = "PASS"
        except Exception as e:  # keep going item by item
            failed += 1
            mark = "FAIL"
            detail = f"{type(e).__name__}: {e}"
            if args.verbose:
                traceback.print_exc()
        elapsed = time.perf_counter() - start
        print(f"[{mark}] {name:<11} {elapsed:5.1f}s  {detail}", flush=True)

    print(f"\n{len(names) - failed}/{len(names)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
