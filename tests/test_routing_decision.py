"""毎ターンのBrain選択（決定論チェックリスト）のテスト。設計書 §3.2

2026-07-18: Brain構成刷新（合意台帳 §9.1）でQwen単一運用へ。escalation/fallback役が
登録簿に存在しない構成が正であるため、単一Brain登録簿でのテストを主に据える。
role制スキーマ自体は将来の複数Brain運用再開に備えて維持しているため、
複数役が揃った登録簿での挙動（旧テストの意図）も別途カバーする。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.routing.decision import decide_brain
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.state.routing_rules import RoutingRules

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _single_registry() -> list[BrainEntry]:
    """実運用のconfig/brains.tomlと同型（Qwen単一・primaryのみ）。"""
    return [BrainEntry("serina-qwen35-unc", "qwen", "local", "primary", -1, -1, "small")]


def _multi_registry() -> list[BrainEntry]:
    """role制スキーマが複数Brainでも機能することを確認するための汎用登録簿
    （§9.1: 将来の複数Brain運用再開に備えて維持している経路）。"""
    return [
        BrainEntry("brain_primary", "qwen", "cloud", "primary", 500, 15, "large"),
        BrainEntry("brain_escalation", "qwen", "cloud", "escalation", 20, 5, "large"),
        BrainEntry("brain_fallback", "qwen", "local", "fallback", -1, -1, "small"),
    ]


def test_normal_turn_uses_primary() -> None:
    result = decide_brain(
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW,
    )
    assert result == "serina-qwen35-unc"


def test_switch_requested_without_fallback_entry_stays_on_primary() -> None:
    """①交代要請チェック: fallback役が登録簿に無ければprimaryが代用される（§9.1）"""
    result = decide_brain(
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", switch_requested=True, now=NOW,
    )
    assert result == "serina-qwen35-unc"


def test_sensitive_topic_without_fallback_entry_stays_on_primary() -> None:
    """②プライバシー判定: fallback役が無い構成でもKeyErrorにならずprimaryへ収束する"""
    rules = RoutingRules()
    rules.tighten("住所")
    result = decide_brain(
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=rules,
        master_utterance="俺の住所覚えてる？", now=NOW,
    )
    assert result == "serina-qwen35-unc"


def test_escalate_requested_without_escalation_entry_is_noop() -> None:
    """③昇格判定: escalation役が登録簿に無ければ昇格要求は無視されprimaryのまま（§9.2品質昇格廃止）"""
    result = decide_brain(
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="人生の岐路の相談", escalate_requested=True, now=NOW,
    )
    assert result == "serina-qwen35-unc"


def test_multi_registry_switch_requested_goes_to_fallback() -> None:
    """role制スキーマ自体は複数Brainでも機能する（§9.1: 将来の再拡張に備えた経路）"""
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", switch_requested=True, now=NOW,
    )
    assert result == "brain_fallback"


def test_multi_registry_escalate_requested_uses_escalation_brain() -> None:
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="人生の岐路の相談", escalate_requested=True, now=NOW,
    )
    assert result == "brain_escalation"


def test_multi_registry_dead_primary_falls_back() -> None:
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW, is_alive=lambda name: name != "brain_primary",
    )
    assert result == "brain_fallback"


def main() -> None:
    tests = [
        test_normal_turn_uses_primary,
        test_switch_requested_without_fallback_entry_stays_on_primary,
        test_sensitive_topic_without_fallback_entry_stays_on_primary,
        test_escalate_requested_without_escalation_entry_is_noop,
        test_multi_registry_switch_requested_goes_to_fallback,
        test_multi_registry_escalate_requested_uses_escalation_brain,
        test_multi_registry_dead_primary_falls_back,
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
