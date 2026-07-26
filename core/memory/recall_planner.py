"""RecallPlanner（想起の前段）。合意台帳 §3.1 / Wave 3 A1。

規則ベースで temporal / entity を検出し、確信が持てないときだけ Brain.judge へ相談する。
Core 内に LLM を持たず、judge は Callable 経由。失敗時は空 plan で現行活性化想起へ。

契約: 時間範囲は必ず UTC ISO 文字列で返す（`FactStore.search_by_time_range` が UTC 前提）。
「昨日」等の暦日判定は Serina 日（`core.state.serina_day`・境界 07:00 ローカル）に一本化し、
ローカル暦の判定でこのモジュールが `astimezone()` を直接呼ぶのは UTC ISO 出力への変換のみ
（`_to_utc_iso`）に限る。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from serina.core.memory.facts import Fact, FactStore
from serina.core.state.serina_day import SERINA_DAY_HOUR, serina_day_id, serina_day_start

TEMPORAL_KEYWORDS: dict[str, Callable[[datetime], tuple[str, str]]] = {}

QUERY_TYPES = frozenset({"semantic", "temporal", "entity", "id"})
MEMORY_TOOL_TYPES = frozenset({
    "memory_search",
    "memory_get",
    "propose_fact",
    "propose_identity_edit",
    "forget_request",
})


def _serina_day_bounds(day_id: date, *, boundary_hour: int = SERINA_DAY_HOUR) -> tuple[str, str]:
    """指定 Serina 日の開始〜終了（UTC ISO文字列）。終了は排他的上限から1マイクロ秒引いた閉区間。"""
    start = serina_day_start(day_id, boundary_hour=boundary_hour)
    end_exclusive = serina_day_start(day_id + timedelta(days=1), boundary_hour=boundary_hour)
    end = end_exclusive - timedelta(microseconds=1)
    return start.isoformat(), end.isoformat()


def _to_utc_iso(now: datetime) -> str:
    return now.astimezone(timezone.utc).isoformat()


def _register_temporal_rules() -> None:
    def yesterday(now: datetime) -> tuple[str, str]:
        return _serina_day_bounds(serina_day_id(now) - timedelta(days=1))

    def day_before_yesterday(now: datetime) -> tuple[str, str]:
        return _serina_day_bounds(serina_day_id(now) - timedelta(days=2))

    def last_week(now: datetime) -> tuple[str, str]:
        start = serina_day_start(serina_day_id(now) - timedelta(days=7))
        return start.isoformat(), _to_utc_iso(now)

    def recent_past(now: datetime) -> tuple[str, str]:
        start = serina_day_start(serina_day_id(now) - timedelta(days=3))
        return start.isoformat(), _to_utc_iso(now)

    TEMPORAL_KEYWORDS["昨日"] = yesterday
    TEMPORAL_KEYWORDS["一昨日"] = day_before_yesterday
    TEMPORAL_KEYWORDS["先週"] = last_week
    TEMPORAL_KEYWORDS["この前"] = recent_past
    TEMPORAL_KEYWORDS["先日"] = recent_past


_register_temporal_rules()

ENTITY_SUFFIX_PATTERN = re.compile(r"([一-龥々〆ヵヶ]{2,12})(?:さん|くん|ちゃん|先生)")
KATAKANA_ENTITY_PATTERN = re.compile(r"[ァ-ヴー]{2,}")
QUOTED_ENTITY_PATTERN = re.compile(r"[「『]([^」』]{1,20})[」』]")


@dataclass(frozen=True)
class RecallQuery:
    type: str
    q: str
    time_range: tuple[str, str] | None = None


@dataclass(frozen=True)
class RecallBudget:
    max_memories: int
    max_tokens: int


@dataclass(frozen=True)
class RecallPlan:
    queries: list[RecallQuery]
    budget: RecallBudget
    why: str


@dataclass(frozen=True)
class RecallBundle:
    """活性化想起結果と facts 併載を分離して保持する。"""

    memories: list
    bundled_facts: list[Fact]
    plan: RecallPlan


DEFAULT_BUDGET = RecallBudget(max_memories=5, max_tokens=2000)
EMPTY_PLAN = RecallPlan(queries=[], budget=DEFAULT_BUDGET, why="")


def _detect_temporal_queries(text: str, now: datetime) -> list[RecallQuery]:
    queries: list[RecallQuery] = []
    for keyword, range_fn in TEMPORAL_KEYWORDS.items():
        if keyword in text:
            time_range = range_fn(now)
            queries.append(RecallQuery(type="temporal", q=keyword, time_range=time_range))
    return queries


def _detect_entity_queries(text: str) -> list[RecallQuery]:
    entities: list[str] = []
    for match in ENTITY_SUFFIX_PATTERN.finditer(text):
        entities.append(match.group(1))
    for match in KATAKANA_ENTITY_PATTERN.finditer(text):
        token = match.group(0)
        if token not in entities:
            entities.append(token)
    for match in QUOTED_ENTITY_PATTERN.finditer(text):
        entities.append(match.group(1))
    return [RecallQuery(type="entity", q=entity) for entity in entities]


def _rule_based_plan(utterance: str, now: datetime) -> RecallPlan | None:
    temporal = _detect_temporal_queries(utterance, now)
    entity = _detect_entity_queries(utterance)
    queries = temporal + entity
    if not queries:
        return None
    why_parts = []
    if temporal:
        why_parts.append("時間表現を検出")
    if entity:
        why_parts.append("固有名詞らしき語を検出")
    return RecallPlan(
        queries=queries,
        budget=DEFAULT_BUDGET,
        why="規則: " + "・".join(why_parts),
    )


JUDGE_PLAN_INSTRUCTION = """
発話から想起計画だけをJSONで返してください。personaは注入していません。
必ず次の形式のJSONブロック1つだけ:
```json
{
  "queries": [{"type": "semantic|temporal|entity|id", "q": "...", "time_range": ["ISO8601開始", "ISO8601終了"]}],
  "budget": {"max_memories": 5, "max_tokens": 2000},
  "why": "日本語1文"
}
```
time_range は temporal のみ。不要なら null または省略可。
"""


def _parse_judge_plan(raw: dict) -> RecallPlan:
    queries_raw = raw.get("queries", [])
    if not isinstance(queries_raw, list):
        raise ValueError("queries が list でない")
    queries: list[RecallQuery] = []
    for item in queries_raw:
        if not isinstance(item, dict):
            continue
        qtype = item.get("type")
        qtext = item.get("q")
        if qtype not in QUERY_TYPES or not isinstance(qtext, str) or not qtext.strip():
            continue
        time_range = None
        tr = item.get("time_range")
        if isinstance(tr, (list, tuple)) and len(tr) == 2:
            if all(isinstance(x, str) for x in tr):
                time_range = (tr[0], tr[1])
        queries.append(RecallQuery(type=qtype, q=qtext.strip(), time_range=time_range))

    budget_raw = raw.get("budget")
    if isinstance(budget_raw, dict):
        max_memories = int(budget_raw.get("max_memories", DEFAULT_BUDGET.max_memories))
        max_tokens = int(budget_raw.get("max_tokens", DEFAULT_BUDGET.max_tokens))
        budget = RecallBudget(max_memories=max_memories, max_tokens=max_tokens)
    else:
        budget = DEFAULT_BUDGET

    why = raw.get("why")
    if not isinstance(why, str):
        why = ""
    return RecallPlan(queries=queries, budget=budget, why=why.strip())


def plan_recall(
    utterance: str,
    *,
    session_tail: str = "",
    now: datetime | None = None,
    judge: Callable[[str], dict] | None = None,
) -> RecallPlan:
    """発話から RecallPlan を組み立てる。失敗時は空 plan。"""
    current = now or datetime.now(timezone.utc)
    rule_plan = _rule_based_plan(utterance, current)
    if rule_plan is not None:
        return rule_plan

    if judge is None:
        return EMPTY_PLAN

    prompt = (
        f"{JUDGE_PLAN_INSTRUCTION}\n\n"
        f"【現在時刻】\n{current.isoformat()}\n\n"
        f"【直近セッション末尾】\n{session_tail or '（なし）'}\n\n"
        f"【発話】\n{utterance}\n"
    )
    try:
        raw = judge(prompt)
        if not isinstance(raw, dict):
            return EMPTY_PLAN
        return _parse_judge_plan(raw)
    except Exception:  # noqa: BLE001
        return EMPTY_PLAN


def resolve_facts_for_plan(plan: RecallPlan, fact_store: FactStore) -> list[Fact]:
    """Planner の temporal / entity クエリに対応する facts を返す（活性化とは別枠）。"""
    found: dict[str, Fact] = {}
    for query in plan.queries:
        if query.type == "entity":
            for fact in fact_store.search_by_entity(query.q):
                found[fact.id] = fact
        elif query.type == "temporal" and query.time_range is not None:
            start, end = query.time_range
            for fact in fact_store.search_by_time_range(start, end):
                found[fact.id] = fact
    return list(found.values())


def merge_memory_recalls(primary, *extras, top_k: int = 5):  # noqa: ANN001
    """複数 recall 結果を id でマージし score 最大を残す。"""
    by_id: dict[int, object] = {}
    for batch in (primary, *extras):
        for record in batch:
            existing = by_id.get(record.id)
            if existing is None or record.score > existing.score:
                by_id[record.id] = record
    merged = sorted(by_id.values(), key=lambda r: r.score, reverse=True)
    return merged[:top_k]
