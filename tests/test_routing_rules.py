"""振り分けルールの逆止弁のテスト。設計書v2 §3.3.1

厳しくなる方向（センシティブ拡大）は自動反映。緩む方向（クラウド解禁拡大）はマスター承認必須。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.state.routing_rules import RoutingRuleError, RoutingRules


def test_initial_rules_detect_nothing_sensitive() -> None:
    rules = RoutingRules()
    assert not rules.is_sensitive("今日の天気はいいね")


def test_tighten_is_applied_automatically() -> None:
    """§3.3.1: 厳しくなる方向は自動で反映"""
    rules = RoutingRules()
    rules.tighten("住所")
    assert rules.is_sensitive("俺の住所覚えてる？")


def test_loosen_without_master_approval_is_rejected() -> None:
    """§3.3.1: 緩む方向はマスター承認が必須"""
    rules = RoutingRules()
    rules.tighten("天気")
    try:
        rules.loosen("天気")
        raise AssertionError("承認なしで緩和が通ってしまった")
    except RoutingRuleError:
        pass
    assert rules.is_sensitive("天気の話をしよう"), "承認なしでは緩和されないはず"


def test_loosen_with_master_approval_is_applied() -> None:
    rules = RoutingRules()
    rules.tighten("天気")
    rules.loosen("天気", master_approved=True)
    assert not rules.is_sensitive("天気の話をしよう")


def main() -> None:
    tests = [
        test_initial_rules_detect_nothing_sensitive,
        test_tighten_is_applied_automatically,
        test_loosen_without_master_approval_is_rejected,
        test_loosen_with_master_approval_is_applied,
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
