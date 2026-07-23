"""tools/migrate_memory_types.py のテスト。設計書2026-07-23改訂: type列をepisodic/semanticへ統合。

promise型（`core.runtime.list_promise_memories_for_pulse()`が唯一の手がかりにしている生きた
機能）が、semanticへ畳まれた後も`metadata.legacy_type`経由で引けることを回帰検査する
（構造レビューで発覚した重大な見落とし。計画書「Phase 1着手直後に発覚した計画漏れ」参照）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from serina.core.runtime import Core
from serina.tools.migrate_memory_types import apply_updates, plan_updates


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        memory_dedup_threshold=0.92,
        memory_max_candidates_per_job=5,
    )


def test_migration_folds_legacy_types_into_semantic_with_legacy_type_tag() -> None:
    store = _fresh_store()
    diary_id = store.add_memory("今日の日記", type="diary", importance=0.5, protection_grade="A")
    event_id = store.add_memory("出来事", type="event", importance=0.5, protection_grade="B")
    promise_id = store.add_memory("約束したこと", type="promise", importance=0.9, protection_grade="S")
    fact_id = store.add_memory("確定事実", type="fact", importance=0.6, protection_grade="B")

    conn = store._connect()  # noqa: SLF001
    try:
        plan = plan_updates(conn)
        apply_updates(conn, plan)
    finally:
        conn.close()

    diary_rec, _ = store.get_memory_by_id(diary_id)  # type: ignore[misc]
    event_rec, _ = store.get_memory_by_id(event_id)  # type: ignore[misc]
    promise_rec, _ = store.get_memory_by_id(promise_id)  # type: ignore[misc]
    fact_rec, _ = store.get_memory_by_id(fact_id)  # type: ignore[misc]

    assert diary_rec.type == "episodic"
    assert event_rec.type == "semantic"
    assert promise_rec.type == "semantic"
    assert fact_rec.type == "semantic"
    # promiseだった行のprotection_gradeは維持されること（type列だけ変わる）
    assert promise_rec.protection_grade == "S"

    promises_after = store.list_by_type_and_legacy_type("semantic", legacy_type="promise")
    assert {r.id for r in promises_after} == {promise_id}
    events_after = store.list_by_type_and_legacy_type("semantic", legacy_type="event")
    assert {r.id for r in events_after} == {event_id}
    # factは本番生成継続分のためlegacy_typeは付かない（=旧promise/event探索に混ざらない）
    facts_as_legacy = store.list_by_type_and_legacy_type("semantic", legacy_type="fact")
    assert fact_id not in {r.id for r in facts_as_legacy}


def test_list_promise_memories_for_pulse_survives_migration() -> None:
    """core.runtime.Core.list_promise_memories_for_pulse()が移行後も機能すること（重大回帰）。"""
    store = _fresh_store()
    kept = store.add_memory("再会の約束", type="promise", importance=0.9, protection_grade="S")
    excluded_low_grade = store.add_memory("軽い約束", type="promise", importance=0.5, protection_grade="B")

    conn = store._connect()  # noqa: SLF001
    try:
        plan = plan_updates(conn)
        apply_updates(conn, plan)
    finally:
        conn.close()

    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), memory_store=store)
    results = core.list_promise_memories_for_pulse()
    ids = {mid for mid, _content in results}

    assert kept in ids
    assert excluded_low_grade not in ids  # protection_grade Bは対象外（元の仕様通り）
