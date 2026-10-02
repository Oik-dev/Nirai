"""Brain登録簿の読み込みテスト。設計書 §3.1「ルーティングは表であってコードではない」

2026-07-18: Brain構成刷新（合意台帳 §9）でBrain単一運用へ。config/brains.tomlは
serina-gemma4-unc（primary/local）1行のみ。escalation/fallback役は登録簿に存在しない
構成を正としてテストする。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.registry import BrainEntry, load_brain_registry


def test_load_registry_returns_single_entry() -> None:
    """§9.1: Brain単一運用のため登録簿は1行のみ"""
    entries = load_brain_registry()
    assert len(entries) == 1
    assert entries[0].name == "serina-gemma4-unc"


def test_entry_has_expected_role_and_location() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    entry = entries["serina-gemma4-unc"]
    assert entry.adapter == "ollama"
    assert entry.location == "local"
    assert entry.role == "primary"


def test_unlimited_quota_is_represented_as_negative_one() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    assert entries["serina-gemma4-unc"].daily_quota == -1
    assert entries["serina-gemma4-unc"].per_minute_quota == -1


def test_role_schema_still_supports_multiple_entries() -> None:
    """§9.1: role制スキーマ自体は将来の複数Brain運用再開に備えて維持する。

    実登録簿は1行だが、BrainEntryを手組みすれば複数役（primary/escalation/fallback）を
    表現できることを型レベルで確認する（登録簿への1行追加だけで拡張できる設計の裏取り）。
    """
    entries = [
        BrainEntry("brain_a", "ollama", "local", "primary", -1, -1, "small"),
        BrainEntry("brain_b", "ollama", "local", "escalation", -1, -1, "small"),
        BrainEntry("brain_c", "ollama", "local", "fallback", -1, -1, "small"),
    ]
    by_role = {e.role: e for e in entries}
    assert by_role["primary"].name == "brain_a"
    assert by_role["escalation"].name == "brain_b"
    assert by_role["fallback"].name == "brain_c"


def main() -> None:
    tests = [
        test_load_registry_returns_single_entry,
        test_entry_has_expected_role_and_location,
        test_unlimited_quota_is_represented_as_negative_one,
        test_role_schema_still_supports_multiple_entries,
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
