"""Brain登録簿の読み込みテスト。設計書 §3.1「ルーティングは表であってコードではない」"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.routing.registry import BrainEntry, load_brain_registry


def test_load_registry_returns_three_initial_entries() -> None:
    """§3.1: 初期登録は3行"""
    entries = load_brain_registry()
    assert len(entries) == 3
    names = {e.name for e in entries}
    assert names == {"gemini_flash_lite", "gemini_flash", "aurora"}


def test_entries_have_expected_roles_and_locations() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    assert entries["gemini_flash_lite"].location == "cloud"
    assert entries["gemini_flash_lite"].role == "primary"
    assert entries["gemini_flash"].role == "escalation"
    assert entries["aurora"].location == "local"
    assert entries["aurora"].role == "fallback"


def test_unlimited_quota_is_represented_as_negative_one() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    assert entries["aurora"].daily_quota == -1


def main() -> None:
    tests = [
        test_load_registry_returns_three_initial_entries,
        test_entries_have_expected_roles_and_locations,
        test_unlimited_quota_is_represented_as_negative_one,
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
