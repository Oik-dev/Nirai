"""保護3原則のテスト。設計書 §4.3（2026-07-19改訂: 同一性は事後監査制）"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.protection import (
    ChangeLog,
    GenerationStore,
    ProtectionError,
    apply_protected_change,
    assert_persona_block_writable,
)
from serina.core.memory.store import MemoryRecord


def _record(protection_grade: str) -> MemoryRecord:
    return MemoryRecord(
        id=1,
        type="fact",
        content="元の内容",
        importance=0.5,
        sensitivity_grade=2,
        protection_grade=protection_grade,
        cosmetic_version=None,
        created_at="2026-01-01T00:00:00+00:00",
        last_accessed="2026-01-01T00:00:00+00:00",
    )


def _stores(tmp: Path) -> tuple[ChangeLog, GenerationStore]:
    return ChangeLog(tmp / "changes.jsonl"), GenerationStore(tmp / "generations.jsonl")


def test_canonical_is_blocked_even_with_master_approval() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        try:
            apply_protected_change(
                record=_record("S"),
                action="書き換え",
                reason="テスト",
                new_content="新しい内容",
                change_log=change_log,
                generation_store=generation_store,
                master_approved=True,
                pinned=True,
            )
            raise AssertionError("正典は承認でも通ってしまった")
        except ProtectionError:
            pass


def test_non_canonical_s_allowed_without_approval_when_conditions_met() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        apply_protected_change(
            record=_record("S"),
            action="書き換え",
            reason="自律改訂",
            new_content="少し変えた",
            change_log=change_log,
            generation_store=generation_store,
            change_ratio=0.2,
            mood_contaminated=False,
        )
        assert len(change_log.read_all()) == 1
        assert generation_store.has_generation(1)


def test_non_canonical_s_rejects_change_ratio_over_20_percent() -> None:
    """改訂幅上限は2026-07-23にマスター判断で40%→20%へ引き下げ（`MAX_AUTONOMOUS_CHANGE_RATIO`）。"""
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        try:
            apply_protected_change(
                record=_record("S"),
                action="書き換え",
                reason="改訂幅超過",
                new_content="大幅変更",
                change_log=change_log,
                generation_store=generation_store,
                change_ratio=0.5,
            )
            raise AssertionError("20%超が通ってしまった")
        except ProtectionError:
            pass


def test_non_canonical_s_rejects_mood_contamination() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        try:
            apply_protected_change(
                record=_record("S"),
                action="書き換え",
                reason="機嫌混入",
                new_content="今日は機嫌悪い口調",
                change_log=change_log,
                generation_store=generation_store,
                change_ratio=0.1,
                mood_contaminated=True,
            )
            raise AssertionError("気分混入が通ってしまった")
        except ProtectionError:
            pass


def test_persona_fixed_block_writable_rejected() -> None:
    try:
        assert_persona_block_writable(mutable=False)
        raise AssertionError("固定ブロック書き込みが通ってしまった")
    except ProtectionError:
        pass


def test_persona_mutable_block_writable_allowed() -> None:
    assert_persona_block_writable(mutable=True)


def test_grade_b_change_records_transparency_report() -> None:
    """原則1: 無言破棄の禁止。統合・削除・書き換えは必ず変更レポートを残す"""
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        apply_protected_change(
            record=_record("B"),
            action="統合",
            reason="重複記憶の統合",
            new_content="統合後の内容",
            change_log=change_log,
            generation_store=generation_store,
        )
        reports = change_log.read_all()
        assert len(reports) == 1
        report = reports[0]
        assert report.action == "統合"
        assert report.reason == "重複記憶の統合"
        assert report.before == "元の内容"
        assert report.after == "統合後の内容"


def test_grade_b_change_saves_generation_before_overwrite() -> None:
    """原則2: 消える前に必ず控えを取る（戻せるなら触っていい）"""
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        apply_protected_change(
            record=_record("B"),
            action="削除",
            reason="忘却ライン到達",
            new_content=None,
            change_log=change_log,
            generation_store=generation_store,
        )
        assert generation_store.has_generation(1)


def test_master_approved_allows_non_canonical_s() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        change_log, generation_store = _stores(Path(tmp))
        apply_protected_change(
            record=_record("S"),
            action="書き換え",
            reason="マスター承認済み",
            new_content="新しい内容",
            change_log=change_log,
            generation_store=generation_store,
            master_approved=True,
        )
        assert len(change_log.read_all()) == 1
