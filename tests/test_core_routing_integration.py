"""Core.turn_routed の配線テスト。設計書v2 §3.2(決定論チェックリスト), §3.4(昇格の持続), §3.5(フォールバック)"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.memory.embedder import OllamaEmbedder
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.routing.quota_ledger import QuotaLedger
from serina.core_v2.routing.registry import BrainEntry
from serina.core_v2.runtime import Core
from serina.core_v2.state.routing_rules import RoutingRules

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _registry() -> list[BrainEntry]:
    return [
        BrainEntry("primary_brain", "gemini", "cloud", "primary", 500, 15, "large"),
        BrainEntry("escalation_brain", "gemini", "cloud", "escalation", 20, 5, "large"),
        BrainEntry("aurora_brain", "aurora", "local", "fallback", -1, -1, "small"),
    ]


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1)


class ScriptedBrain:
    def __init__(self, script: dict | None = None, raise_error: bool = False) -> None:
        self.script = script
        self.raise_error = raise_error
        self.call_count = 0

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.call_count += 1
        if self.raise_error:
            raise RuntimeError("クラウドの安全フィルタに拒否された")
        return self.script


def _report(over_capacity: bool, fusen_list: list[dict] | None = None) -> dict:
    return {
        "reply": "了解です",
        "fusen_list": fusen_list or [],
        "self_assessment": {"over_capacity": over_capacity, "reason": "テスト"},
    }


def _core(brains: dict, memory_store: MemoryStore | None = None) -> Core:
    return Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(), brains=brains,
        memory_store=memory_store,
    )


class _BrokenEmbedder(OllamaEmbedder):
    def __init__(self) -> None:
        def always_fail(model: str, text: str) -> list[float]:
            raise RuntimeError("Ollamaが瞬断した")
        super().__init__(call_fn=always_fail)


def test_normal_turn_uses_primary_brain() -> None:
    primary = ScriptedBrain(_report(over_capacity=False))
    escalation = ScriptedBrain(_report(over_capacity=False))
    aurora = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": primary, "escalation_brain": escalation, "aurora_brain": aurora})

    core.turn_routed("こんにちは", now=NOW)

    assert primary.call_count == 1
    assert escalation.call_count == 0
    assert aurora.call_count == 0


def test_escalation_persists_until_self_reported_deescalation() -> None:
    """§3.4: 上位残弾が重い話題の終了後に漏れ続けるのを防ぐ→自己申告まで昇格を維持する"""
    primary = ScriptedBrain(_report(over_capacity=True))  # 1ターン目でover_capacity申告
    escalation_still_heavy = ScriptedBrain(_report(over_capacity=True))
    core = _core({"primary_brain": primary, "escalation_brain": escalation_still_heavy, "aurora_brain": ScriptedBrain(_report(False))})

    core.turn_routed("人生の岐路の相談", now=NOW)  # primaryが手に余ると申告
    assert core.current_tier == "escalation"

    core.turn_routed("続きの相談", now=NOW)  # 2ターン目: 自動でescalationへ
    assert escalation_still_heavy.call_count == 1
    assert primary.call_count == 1, "昇格中はprimaryへ戻らない"
    assert core.current_tier == "escalation", "まだ手に余ると申告しているので昇格維持"


def test_deescalation_when_escalation_brain_reports_normal() -> None:
    escalation = ScriptedBrain(_report(over_capacity=False))  # もう日常会話に戻ったと申告
    core = _core({"primary_brain": ScriptedBrain(_report(True)), "escalation_brain": escalation, "aurora_brain": ScriptedBrain(_report(False))})
    core.current_tier = "escalation"  # 既に昇格中と仮定

    core.turn_routed("もう大丈夫、ありがとう", now=NOW)

    assert escalation.call_count == 1
    assert core.current_tier == "primary", "降格申告により主戦力へ帰還すべき"


def test_cloud_rejection_falls_back_to_aurora_same_turn_and_tightens_rule() -> None:
    """§3.5: クラウドの拒否→同ターンAurora代打＋振り分けルールを研ぐ（逆止弁）"""
    primary = ScriptedBrain(raise_error=True)
    aurora = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": primary, "escalation_brain": ScriptedBrain(_report(False)), "aurora_brain": aurora})

    result = core.turn_routed("危険な話題かもしれない発言", now=NOW)

    assert aurora.call_count == 1
    assert result.report.reply == "了解です"
    assert core.routing_rules.is_sensitive("危険な話題かもしれない発言"), "拒否事故が振り分けルールに刻まれるべき"


def test_never_crashes_when_aurora_fallback_extraction_always_fails() -> None:
    """§3.2最終防衛線: primaryが拒否→Aurora代打も全滅(adapter raise)しても沈黙しない"""
    class RaisingBrain:
        def converse(self, pack):  # noqa: ANN001
            raise RuntimeError("完全に応答不能")

    primary = ScriptedBrain(raise_error=True)
    aurora = RaisingBrain()
    core = _core({"primary_brain": primary, "escalation_brain": ScriptedBrain(_report(False)), "aurora_brain": aurora})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


def test_never_crashes_when_aurora_fallback_returns_malformed_report() -> None:
    """§3.2最終防衛線: Auroraが書式違反の報告書を返しても沈黙しない"""
    class MalformedBrain:
        def converse(self, pack):  # noqa: ANN001
            return {"reply": "", "fusen_list": [], "self_assessment": {"over_capacity": "いいえ", "reason": "x"}}

    primary = ScriptedBrain(raise_error=True)
    aurora = MalformedBrain()
    core = _core({"primary_brain": primary, "escalation_brain": ScriptedBrain(_report(False)), "aurora_brain": aurora})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


def test_memory_failure_does_not_break_conversation() -> None:
    """§2.4: 裏方便が遅れても会話は壊れない。想起・記憶書き戻しが失敗しても返答は返るべき"""
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    broken_store = MemoryStore(str(db_path), embedder=_BrokenEmbedder(), vector_dim=4)

    primary = ScriptedBrain(_report(
        over_capacity=False,
        fusen_list=[{
            "kind": "記憶候補", "version": 1,
            "content": {"content": "散歩が好き", "type": "fact", "importance": 0.5,
                        "sensitivity_grade": 0, "quote": "散歩が好きなんだ"},
            "confidence": 0.9,
        }],
    ))
    core = _core(
        {"primary_brain": primary, "escalation_brain": ScriptedBrain(_report(False)), "aurora_brain": ScriptedBrain(_report(False))},
        memory_store=broken_store,
    )

    result = core.turn_routed("散歩が好きなんだ", now=NOW)

    assert result.report.reply, "記憶が壊れていても会話の返答は届くべき"


def test_switch_request_fusen_routes_next_turn_to_aurora() -> None:
    """§3.4交代要請: 次ターンは直接Auroraへ"""
    primary = ScriptedBrain(_report(
        over_capacity=False,
        fusen_list=[{"kind": "交代要請", "version": 1, "content": {"理由": "能力不足"}, "confidence": 0.9}],
    ))
    aurora = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": primary, "escalation_brain": ScriptedBrain(_report(False)), "aurora_brain": aurora})

    core.turn_routed("最初の発言", now=NOW)
    assert primary.call_count == 1

    core.turn_routed("次の発言", now=NOW)
    assert aurora.call_count == 1, "交代要請の次ターンはAuroraへ直行すべき"


def main() -> None:
    tests = [
        test_normal_turn_uses_primary_brain,
        test_escalation_persists_until_self_reported_deescalation,
        test_deescalation_when_escalation_brain_reports_normal,
        test_cloud_rejection_falls_back_to_aurora_same_turn_and_tightens_rule,
        test_never_crashes_when_aurora_fallback_extraction_always_fails,
        test_never_crashes_when_aurora_fallback_returns_malformed_report,
        test_memory_failure_does_not_break_conversation,
        test_switch_request_fusen_routes_next_turn_to_aurora,
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
