"""想起→パック組み立て→Brain到達の通し検証。設計書 §3.3(個人情報フィルタ), §4.2, §4.6-2

パック層単体テストでは検出できなかった「Coreが宛先を渡さず全記憶が間引かれる」
（記憶ブラックアウト）と「クラウド宛でローカルターンが伏せられない」の回帰防止。
本番初期状態（全記憶が機微等級2）を模したstoreで、宛先ごとの載る/載らないを通しで確認する。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import ThresholdsConfig
from serina.core.memory.store import MemoryRecord
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.runtime import Core
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.session import Turn

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _memory(id_: int, content: str, grade: int) -> MemoryRecord:
    return MemoryRecord(
        id=id_, type="fact", content=content, importance=0.5,
        sensitivity_grade=grade, protection_grade="B", cosmetic_version=None,
        created_at="2026-01-01T00:00:00+00:00", last_accessed="2026-01-01T00:00:00+00:00",
    )


class GradeTwoOnlyStore:
    """本番初期状態の模型: 移行直後は全866件が機微等級2（migrate_memory_schemaのDEFAULT 2）"""

    def recall(self, query: str, top_k: int = 5) -> list[MemoryRecord]:  # noqa: ARG002
        return [_memory(1, "マスターとの再会の約束", grade=2)]


class PackCapturingBrain:
    def __init__(self, raise_cls: type[Exception] | None = None) -> None:
        self.packs = []
        self.raise_cls = raise_cls

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.packs.append(pack)
        if self.raise_cls is not None:
            raise self.raise_cls("Brain呼び出しが失敗した")
        return {
            "reply": "了解です", "fusen_list": [],
            "self_assessment": {"over_capacity": False, "reason": "テスト"},
        }


def _core(primary: PackCapturingBrain, aurora: PackCapturingBrain) -> Core:
    return Core(
        persona_text="人格", absolute_rules="ルール",
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        memory_store=GradeTwoOnlyStore(),
        registry=[
            BrainEntry("primary_brain", "gemini", "cloud", "primary", 500, 15, "large"),
            BrainEntry("escalation_brain", "gemini", "cloud", "escalation", 20, 5, "large"),
            BrainEntry("aurora_brain", "aurora", "local", "fallback", -1, -1, "small"),
        ],
        quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        brains={"primary_brain": primary, "escalation_brain": PackCapturingBrain(), "aurora_brain": aurora},
    )


def test_local_pack_contains_grade2_memories_end_to_end() -> None:
    """記憶ブラックアウト回帰防止: 全記憶が等級2でも、ローカル(Aurora)宛パックには記憶が載る"""
    primary = PackCapturingBrain()
    aurora = PackCapturingBrain()
    core = _core(primary, aurora)
    core.pending_switch_request = True  # 次ターンをAurora直行にして局所化

    core.turn_routed("約束のこと覚えてる？", now=NOW)

    assert len(aurora.packs) == 1
    assert any("再会の約束" in m for m in aurora.packs[0].long_term_memories), (
        "ローカル宛パックに等級2の記憶が載っていない（記憶ブラックアウトの再発）"
    )


def test_cloud_pack_drops_grade2_memories_end_to_end() -> None:
    """§4.2: クラウド(Gemini)宛パックには等級2の記憶を載せない"""
    primary = PackCapturingBrain()
    core = _core(primary, PackCapturingBrain())

    core.turn_routed("こんにちは", now=NOW)

    assert len(primary.packs) == 1
    assert not any("再会の約束" in m for m in primary.packs[0].long_term_memories), (
        "等級2の記憶がクラウド宛パックに混入している（設計書 §4.2違反）"
    )


def test_cloud_pack_masks_local_turns_end_to_end() -> None:
    """§3.3第3経路: クラウド宛パックでは過去のローカル担当ターンの原文を伏せる"""
    primary = PackCapturingBrain()
    core = _core(primary, PackCapturingBrain())
    core.session.add_turn(Turn(speaker="master", text="俺の住所は横浜市○○区△△1-2-3", location="local"))
    core.session.add_turn(Turn(speaker="serina", text="覚えたよ、△△1-2-3だね", location="local"))

    core.turn_routed("今日はいい天気だね", now=NOW)

    recent = primary.packs[0].recent_turns_text
    assert "△△1-2-3" not in recent, "ローカル担当ターンの原文がクラウド宛パックに漏れている"
    assert "（ローカルで交わした会話）" in recent


def test_fallback_rebuilds_pack_for_local_destination() -> None:
    """クラウド失敗→Aurora代打のとき、パックはローカル宛として組み直される（使い回さない）"""
    primary = PackCapturingBrain(raise_cls=ConnectionError)
    aurora = PackCapturingBrain()
    core = _core(primary, aurora)
    core.session.add_turn(Turn(speaker="master", text="秘密の番地は△△1-2-3", location="local"))

    core.turn_routed("さっきの話の続きだけど", now=NOW)

    assert "△△1-2-3" not in primary.packs[0].recent_turns_text, "クラウド宛パックでは伏せる"
    assert "△△1-2-3" in aurora.packs[0].recent_turns_text, "ローカル宛パックでは原文が見える"
    assert any("再会の約束" in m for m in aurora.packs[0].long_term_memories), (
        "代打Auroraのパックがクラウド宛のまま使い回されている（記憶が間引かれた）"
    )


def test_turns_are_stamped_with_handling_brain_location() -> None:
    """§3.3第3経路の前提: ターンには担当Brainの所在が刻まれる"""
    primary = PackCapturingBrain()
    aurora = PackCapturingBrain()
    core = _core(primary, aurora)

    core.turn_routed("こんにちは", now=NOW)
    assert all(t.location == "cloud" for t in core.session.turns), "クラウド担当ターンはcloudと刻む"

    core.pending_switch_request = True
    core.turn_routed("次はローカルで", now=NOW)
    assert all(t.location == "local" for t in core.session.turns[-2:]), "ローカル担当ターンはlocalと刻む"


def main() -> None:
    tests = [
        test_local_pack_contains_grade2_memories_end_to_end,
        test_cloud_pack_drops_grade2_memories_end_to_end,
        test_cloud_pack_masks_local_turns_end_to_end,
        test_fallback_rebuilds_pack_for_local_destination,
        test_turns_are_stamped_with_handling_brain_location,
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
