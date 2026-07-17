"""既存記憶の機微査定テスト。設計書 §4.6-3。

core/chores/sensitivity_assessment.py の査定ロジック（Aurora判定→is_sensitive()を
下限フロアに適用→化粧版の二重検証）と、core/context/pack.py の保険修正
（化粧版無し機微1はクラウド宛パックから除外）を検査する。LLM不要（call_fnをスタブ化）。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.sensitivity_assessment import (
    assess_memory,
    run_sensitivity_assessment_chunk,
)
from serina.core.context.pack import _filter_memories_for_pack
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryRecord, MemoryStore
from serina.core.state.routing_rules import RoutingRules


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0] if "散歩" in text else [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4,
    )


def _fresh_change_log() -> ChangeLog:
    return ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")


def _record(
    *, memory_id: int = 1, content: str = "散歩が好きだという話", sensitivity_grade: int = 2,
    protection_grade: str = "B", cosmetic_version: str | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        id=memory_id,
        type="fact",
        content=content,
        importance=0.5,
        sensitivity_grade=sensitivity_grade,
        protection_grade=protection_grade,
        cosmetic_version=cosmetic_version,
        created_at="2026-01-01T00:00:00+00:00",
        last_accessed="2026-01-01T00:00:00+00:00",
        sensitivity_assessed=False,
    )


def _script_call_fn(grade: int, cosmetic_version: str | None = None):
    def call_fn(prompt: str) -> str:
        return json.dumps({"grade": grade, "cosmetic_version": cosmetic_version})

    return call_fn


def test_assess_memory_grade0_accepted_as_is() -> None:
    """パターンに引っかからない等級0判定はそのまま採用される。"""
    outcome = assess_memory(
        _record(content="散歩が好き"),
        call_fn=_script_call_fn(0),
        routing_rules=RoutingRules(),
    )
    assert outcome.assessed is True
    assert outcome.final_grade == 0
    assert outcome.cosmetic_version is None


def test_assess_memory_grade1_with_safe_cosmetic_accepted() -> None:
    """等級1判定＋is_sensitive()を通らない化粧版は、そのまま等級1で確定する。"""
    outcome = assess_memory(
        _record(content="自宅は横浜市○○区△△1-2-3"),
        call_fn=_script_call_fn(1, cosmetic_version="自宅は横浜市"),
        routing_rules=RoutingRules(),
    )
    assert outcome.assessed is True
    assert outcome.final_grade == 1
    assert outcome.cosmetic_version == "自宅は横浜市"


def test_assess_memory_grade1_without_cosmetic_falls_back_to_grade2() -> None:
    """化粧版が要求どおり得られなかった等級1判定は、等級2に据え置かれる（確定させない）。"""
    outcome = assess_memory(
        _record(content="職業の詳細"),
        call_fn=_script_call_fn(1, cosmetic_version=None),
        routing_rules=RoutingRules(),
    )
    assert outcome.assessed is True
    assert outcome.final_grade == 2
    assert outcome.cosmetic_version is None


def test_assess_memory_cosmetic_still_sensitive_is_rejected_and_grade_falls_to_2() -> None:
    """化粧版自体がis_sensitive()を通らない（まだ機微の形が残る）場合は、化粧版を破棄し
    等級2に据え置く（12B地元モデルの化粧版品質が不安定でも漏れない方向に倒す）。"""
    outcome = assess_memory(
        _record(content="職業についての話"),
        call_fn=_script_call_fn(1, cosmetic_version="携帯は09012345678のまま"),
        routing_rules=RoutingRules(),
    )
    assert outcome.assessed is True
    assert outcome.final_grade == 2
    assert outcome.cosmetic_version is None
    assert outcome.cosmetic_rejected is True


def test_assess_memory_is_sensitive_floor_prevents_downgrade() -> None:
    """is_sensitive()は下限フロア。Auroraが0/1と自己申告しても、パターンに引っかかれば
    絶対に2未満へは下げない（downgrade禁止）。"""
    outcome = assess_memory(
        _record(content="APIキーはsk-ant-abcdefghijklmnopqrstuvwx"),
        call_fn=_script_call_fn(0),
        routing_rules=RoutingRules(),
    )
    assert outcome.assessed is True
    assert outcome.final_grade == 2


def test_assess_memory_malformed_json_leaves_unassessed() -> None:
    """JSON解釈失敗は未査定のまま返す（次回再挑戦。蒸留消化と同じ電源断耐性の思想）。"""
    outcome = assess_memory(
        _record(),
        call_fn=lambda prompt: "壊れたJSON",
        routing_rules=RoutingRules(),
        max_retries=1,
    )
    assert outcome.assessed is False
    assert outcome.failure_reason is not None


def test_assess_memory_retries_until_valid_json() -> None:
    """§5.5-7と同思想: Auroraの書式崩れは査定側でもリトライする。"""
    calls: list[str] = []

    def flaky(prompt: str) -> str:
        calls.append(prompt)
        if len(calls) < 3:
            return "説明だけの壊れた応答"
        return json.dumps({"grade": 0, "cosmetic_version": None})

    outcome = assess_memory(
        _record(), call_fn=flaky, routing_rules=RoutingRules(), max_retries=3,
    )

    assert outcome.assessed is True
    assert outcome.final_grade == 0
    assert len(calls) == 3
    assert "JSON" in calls[1]  # 2回目以降は書式再強調が入る


def test_assess_memory_exhausted_retries_keeps_failure_reason() -> None:
    outcome = assess_memory(
        _record(),
        call_fn=lambda prompt: "壊れたJSON",
        routing_rules=RoutingRules(),
        max_retries=2,
    )
    assert outcome.assessed is False
    assert "JSON" in (outcome.failure_reason or "")


def test_run_sensitivity_assessment_chunk_records_change_log_after_db_update() -> None:
    """査定結果は変更レポートに記録される（§4.6-3、保護3原則の原則1）。
    DB更新(update_sensitivity)成功後に記録する設計のため、chunk関数側で検査する
    （serina-code-reviewerレビューM-1: 査定"したつもり"がDB未反映のまま監査ログに
    残る乖離を避けるため、assess_memory単体では記録しない）。"""
    store = _fresh_store()
    store.add_memory("散歩が好きだという話", type="fact")
    change_log = _fresh_change_log()

    run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(), change_log=change_log, limit=1,
    )

    reports = change_log.read_all()
    assert len(reports) == 1
    assert reports[0].action == "機微査定"
    assert reports[0].target_id == 1


def test_run_sensitivity_assessment_chunk_updates_store_and_marks_assessed() -> None:
    store = _fresh_store()
    memory_id = store.add_memory("散歩が好きだという話", type="fact")

    summary = run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(),
        change_log=_fresh_change_log(), limit=1,
    )

    assert summary.total_assessed == 1
    remaining = store.get_unassessed_memories(limit=10)
    assert remaining == []
    recalled = store.recall("散歩の話題", top_k=1)
    assert recalled[0].sensitivity_grade == 0
    assert recalled[0].sensitivity_assessed is True


def test_run_sensitivity_assessment_chunk_excludes_protection_grade_s() -> None:
    """正典由来の固定9件（保護等級S）は機微査定の対象外（マスター確認済み）。"""
    store = _fresh_store()
    store.add_memory("散歩が好きだという話", type="fact", protection_grade="S")

    summary = run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(),
        change_log=_fresh_change_log(), limit=10,
    )

    assert summary.processed == []
    assert summary.failed == []


def test_run_sensitivity_assessment_chunk_respects_limit() -> None:
    store = _fresh_store()
    store.add_memory("散歩1回目", type="fact")
    store.add_memory("散歩2回目", type="fact")

    summary = run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(),
        change_log=_fresh_change_log(), limit=1,
    )

    assert summary.total_assessed == 1
    assert len(store.get_unassessed_memories(limit=10)) == 1


def test_run_sensitivity_assessment_chunk_shelves_after_three_failures() -> None:
    """2026-07-12決定: 機微査定は車線が"local"1本のみのため車線振替は無く、
    既定回数(3回)連続失敗した記憶は棚上げ棚へ移動する（原則1: 無言破棄禁止）。"""
    store = _fresh_store()
    memory_id = store.add_memory("散歩が好きだという話", type="fact")
    chore_box = ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")
    change_log = _fresh_change_log()

    for _ in range(3):
        summary = run_sensitivity_assessment_chunk(
            store, call_fn=lambda prompt: "壊れたJSON", routing_rules=RoutingRules(),
            change_log=change_log, limit=1, chore_box=chore_box, max_retries=1,
        )

    assert summary.failed == [memory_id]
    assert chore_box.shelved_assessment_ids() == {memory_id}
    reports = change_log.read_all()
    assert any(r.action == "機微査定棚上げ" for r in reports)
    # 棚上げ後は get_unassessed_memories からも除外される（次回以降の先頭詰まり解消）。
    assert store.get_unassessed_memories(limit=10, exclude_ids=chore_box.shelved_assessment_ids()) == []


def test_run_sensitivity_assessment_chunk_records_failure_reason() -> None:
    """失敗理由をchange_logとassessment_failuresに残す（原則1: 無言破棄禁止の診断強化）。"""
    store = _fresh_store()
    memory_id = store.add_memory("散歩が好きだという話", type="fact")
    chore_box = ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")
    change_log = _fresh_change_log()

    run_sensitivity_assessment_chunk(
        store, call_fn=lambda prompt: "壊れたJSON", routing_rules=RoutingRules(),
        change_log=change_log, limit=1, chore_box=chore_box, max_retries=1,
    )

    reports = change_log.read_all()
    assert any(r.action == "機微査定失敗" and r.target_id == memory_id for r in reports)
    fail_report = next(r for r in reports if r.action == "機微査定失敗")
    assert "JSON" in fail_report.reason
    assert chore_box.assessment_failure_reason(memory_id) is not None
    assert "JSON" in (chore_box.assessment_failure_reason(memory_id) or "")


def test_unshelve_assessment_resets_failure_and_allows_retry() -> None:
    store = _fresh_store()
    memory_id = store.add_memory("散歩が好きだという話", type="fact")
    chore_box = ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")
    change_log = _fresh_change_log()
    for _ in range(3):
        run_sensitivity_assessment_chunk(
            store, call_fn=lambda prompt: "壊れたJSON", routing_rules=RoutingRules(),
            change_log=change_log, limit=1, chore_box=chore_box, max_retries=1,
        )
    assert chore_box.shelved_assessment_ids() == {memory_id}

    from serina.core.chores.sensitivity_assessment import unshelve_assessments_with_report

    unshelved = unshelve_assessments_with_report(
        chore_box, change_log, [memory_id], reason="リトライ実装後の再挑戦",
    )

    assert unshelved == [memory_id]
    assert chore_box.shelved_assessment_ids() == set()
    assert any(r.action == "機微査定棚上げ解除" for r in change_log.read_all())
    summary = run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(),
        change_log=change_log, limit=1, chore_box=chore_box,
    )
    assert summary.total_assessed == 1


def test_run_sensitivity_assessment_chunk_noop_when_empty() -> None:
    store = _fresh_store()

    summary = run_sensitivity_assessment_chunk(
        store, call_fn=_script_call_fn(0), routing_rules=RoutingRules(),
        change_log=_fresh_change_log(), limit=1,
    )

    assert summary.processed == []


# --- pack.py 保険修正の回帰（安全性の心臓その2） ---

def test_pack_excludes_grade1_without_cosmetic_from_cloud_pack() -> None:
    """化粧版が無い機微1は、クラウド宛パックから除外される（原文素通りの穴を塞いだ回帰）。"""
    records = [_record(content="職業の詳細です", sensitivity_grade=1, cosmetic_version=None)]

    lines = _filter_memories_for_pack(records, destination_location="cloud")

    assert lines == []


def test_pack_includes_grade1_with_cosmetic_in_cloud_pack() -> None:
    """化粧版がある機微1は化粧版のみがクラウド宛パックに載る。"""
    records = [_record(content="自宅は横浜市○○区△△1-2-3", sensitivity_grade=1, cosmetic_version="自宅は横浜市")]

    lines = _filter_memories_for_pack(records, destination_location="cloud")

    assert lines == ["自宅は横浜市"]


def test_pack_local_destination_still_gets_raw_content_regardless_of_grade() -> None:
    """宛先localでは全等級を原文で載せる（§4.6-2、既存挙動に回帰が無いことの確認）。"""
    records = [_record(content="職業の詳細です", sensitivity_grade=1, cosmetic_version=None)]

    lines = _filter_memories_for_pack(records, destination_location="local")

    assert lines == ["職業の詳細です"]


def main() -> None:
    tests = [
        test_assess_memory_grade0_accepted_as_is,
        test_assess_memory_grade1_with_safe_cosmetic_accepted,
        test_assess_memory_grade1_without_cosmetic_falls_back_to_grade2,
        test_assess_memory_cosmetic_still_sensitive_is_rejected_and_grade_falls_to_2,
        test_assess_memory_is_sensitive_floor_prevents_downgrade,
        test_assess_memory_malformed_json_leaves_unassessed,
        test_assess_memory_retries_until_valid_json,
        test_assess_memory_exhausted_retries_keeps_failure_reason,
        test_run_sensitivity_assessment_chunk_records_change_log_after_db_update,
        test_run_sensitivity_assessment_chunk_updates_store_and_marks_assessed,
        test_run_sensitivity_assessment_chunk_excludes_protection_grade_s,
        test_run_sensitivity_assessment_chunk_respects_limit,
        test_run_sensitivity_assessment_chunk_shelves_after_three_failures,
        test_run_sensitivity_assessment_chunk_records_failure_reason,
        test_unshelve_assessment_resets_failure_and_allows_retry,
        test_run_sensitivity_assessment_chunk_noop_when_empty,
        test_pack_excludes_grade1_without_cosmetic_from_cloud_pack,
        test_pack_includes_grade1_with_cosmetic_in_cloud_pack,
        test_pack_local_destination_still_gets_raw_content_regardless_of_grade,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
