"""毎ターンのBrain選択のテスト。設計書 §3.2 / §9（Qwen単一・会話クラウド振り分け退役）。

外への相談は Brain 切替ではなく Gemini アドバイザー Skill。
decide_brain は primary の残弾・生死と全滅時 fallback のみ。
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
    return [BrainEntry("serina-qwen35-unc", "qwen", "local", "primary", -1, -1, "small")]


def _multi_registry() -> list[BrainEntry]:
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


def test_legacy_switch_flag_is_ignored() -> None:
    """交代要請フラグは互換シグネチャとして残すが、振り分けには使わない。"""
    result = decide_brain(
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", switch_requested=True, now=NOW,
    )
    assert result == "serina-qwen35-unc"


def test_sensitive_topic_does_not_reroute() -> None:
    """機微語があっても会話 Brain は切り替えない（門番はアドバイザー query 用）。"""
    rules = RoutingRules()
    rules.tighten("住所")
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=rules,
        master_utterance="俺の住所覚えてる？", now=NOW,
    )
    assert result == "brain_primary"


def test_legacy_escalate_flag_is_ignored() -> None:
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="人生の岐路の相談", escalate_requested=True, now=NOW,
    )
    assert result == "brain_primary"


def test_multi_registry_dead_primary_falls_back() -> None:
    result = decide_brain(
        registry=_multi_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        master_utterance="こんにちは", now=NOW, is_alive=lambda name: name != "brain_primary",
    )
    assert result == "brain_fallback"


def main() -> None:
    tests = [
        test_normal_turn_uses_primary,
        test_legacy_switch_flag_is_ignored,
        test_sensitive_topic_does_not_reroute,
        test_legacy_escalate_flag_is_ignored,
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
