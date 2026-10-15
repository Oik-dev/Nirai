"""Brain登録簿の読み込みテスト。設計書 §3.1「ルーティングは表であってコードではない」

config/brains.toml はローカルGemmaと性能順のクラウドGemini 5行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.registry import BrainEntry, load_brain_registry


def test_load_registry_returns_single_entry() -> None:
    """脳が二つ登録され、それぞれ違う場所にある。"""
    entries = load_brain_registry()
    assert len(entries) == 6
    assert entries[0].name == "serina-gemma4-unc"
    assert entries[1].name == 'gemini-3.8-flash'
    assert entries[1].location == 'cloud'
    assert [e.name for e in entries[1:]] == [
        'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash',
        'gemini-3-flash-preview', 'gemini-3.5-flash-lite',
    ]


def test_entry_has_expected_role_and_location() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    entry = entries["serina-gemma4-unc"]
    assert entry.adapter == "ollama"
    assert entry.location == "local"
    assert entry.role == "primary"


def test_provider_limits_are_not_in_our_registry() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    assert set(vars(entries["gemini-3.8-flash"])) == {
        'name', 'adapter', 'location', 'role', 'context_size', 'model'}


def test_role_schema_still_supports_multiple_entries() -> None:
    """§9.1: role制スキーマ自体は将来の複数Brain運用再開に備えて維持する。

    実登録簿は1行だが、BrainEntryを手組みすれば複数役（primary/escalation/fallback）を
    表現できることを型レベルで確認する（登録簿への1行追加だけで拡張できる設計の裏取り）。
    """
    entries = [
        BrainEntry("brain_a", "ollama", "local", "primary", "small"),
        BrainEntry("brain_b", "ollama", "local", "escalation", "small"),
        BrainEntry("brain_c", "ollama", "local", "fallback", "small"),
    ]
    by_role = {e.role: e for e in entries}
    assert by_role["primary"].name == "brain_a"
    assert by_role["escalation"].name == "brain_b"
    assert by_role["fallback"].name == "brain_c"


def main() -> None:
    tests = [
        test_load_registry_returns_single_entry,
        test_entry_has_expected_role_and_location,
        test_provider_limits_are_not_in_our_registry,
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
