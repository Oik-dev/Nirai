"""保護3原則のテスト。設計書 §4.3

1.透明性(無言破棄禁止・変更レポート) 2.可逆性(世代保存) 3.同一性(等級Sはマスター承認のみ)
"""

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


def test_grade_s_change_without_approval_is_blocked() -> None:
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
            )
            raise AssertionError("等級Sの変更が承認なしで通ってしまった")
        except ProtectionError:
            pass


def test_grade_s_change_with_approval_is_allowed() -> None:
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


def main() -> None:
    tests = [
        test_grade_s_change_without_approval_is_blocked,
        test_grade_s_change_with_approval_is_allowed,
        test_grade_b_change_records_transparency_report,
        test_grade_b_change_saves_generation_before_overwrite,
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
