"""毎ターンのBrain選択（決定論チェックリスト）のテスト。設計書 §3.2"""

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


def _registry() -> list[BrainEntry]:
    return [
        BrainEntry("gemini_flash_lite", "gemini", "cloud", "primary", 500, 15, "large"),
        BrainEntry("gemini_flash", "gemini", "cloud", "escalation", 20, 5, "large"),
        BrainEntry("aurora", "aurora", "local", "fallback", -1, -1, "small"),
    ]


def test_normal_turn_uses_primary() -> None:
    result = decide_brain(
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW,
    )
    assert result == "gemini_flash_lite"


def test_switch_requested_goes_straight_to_aurora() -> None:
    """①交代要請チェック: 前ターンの自己評価に従う"""
    result = decide_brain(
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", switch_requested=True, now=NOW,
    )
    assert result == "aurora"


def test_sensitive_topic_goes_to_aurora() -> None:
    """②プライバシー判定: センシティブ話題はローカル確定"""
    rules = RoutingRules()
    rules.tighten("住所")
    result = decide_brain(
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=rules,
        master_utterance="俺の住所覚えてる？", now=NOW,
    )
    assert result == "aurora"


def test_escalate_requested_uses_escalation_brain_when_quota_available() -> None:
    """③昇格判定: 自己申告で上位モデルへ"""
    result = decide_brain(
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="人生の岐路の相談", escalate_requested=True, now=NOW,
    )
    assert result == "gemini_flash"


def test_escalation_quota_exhausted_falls_back_to_primary() -> None:
    """④残弾チェック: 昇格先の弾切れなら本命(primary)へ"""
    ledger = QuotaLedger()
    for _ in range(20):
        ledger.record_use("gemini_flash", now=NOW)
    result = decide_brain(
        registry=_registry(), quota_ledger=ledger, routing_rules=RoutingRules(),
        master_utterance="人生の岐路の相談", escalate_requested=True, now=NOW,
    )
    assert result == "gemini_flash_lite"


def test_primary_quota_exhausted_falls_back_to_aurora() -> None:
    """④残弾チェック: 本命も弾切れなら最終防衛線Auroraへ"""
    ledger = QuotaLedger()
    for _ in range(500):
        ledger.record_use("gemini_flash_lite", now=NOW)
    result = decide_brain(
        registry=_registry(), quota_ledger=ledger, routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW,
    )
    assert result == "aurora"


def test_dead_brain_falls_back_to_aurora() -> None:
    """⑤生死確認: 選ばれたBrainが応答不能なら次点へ（最終的にAurora）"""
    result = decide_brain(
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW, is_alive=lambda name: name != "gemini_flash_lite",
    )
    assert result == "aurora"


def main() -> None:
    tests = [
        test_normal_turn_uses_primary,
        test_switch_requested_goes_straight_to_aurora,
        test_sensitive_topic_goes_to_aurora,
        test_escalate_requested_uses_escalation_brain_when_quota_available,
        test_escalation_quota_exhausted_falls_back_to_primary,
        test_primary_quota_exhausted_falls_back_to_aurora,
        test_dead_brain_falls_back_to_aurora,
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
