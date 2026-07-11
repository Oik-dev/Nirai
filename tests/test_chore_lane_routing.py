"""裏方便の断片ごとの車線振り分けテスト。設計書v2 §2.4/§2.6, 2026-07-12実装。

センシティブな断片は絶対にlane="cloud"にならない（回帰テスト）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.chores.chore_box import ChoreBox
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.routing.quota_ledger import QuotaLedger
from serina.core_v2.routing.registry import BrainEntry
from serina.core_v2.runtime import Core
from serina.core_v2.state.routing_rules import RoutingRules
from serina.core_v2.state.session import Turn


def _registry() -> list[BrainEntry]:
    return [
        BrainEntry("primary_brain", "gemini", "cloud", "primary", 500, 15, "large"),
        BrainEntry("escalation_brain", "gemini", "cloud", "escalation", 20, 5, "large"),
        BrainEntry("aurora_brain", "aurora", "local", "fallback", -1, -1, "small"),
    ]


def _core_with_chore_box() -> tuple[Core, ChoreBox]:
    db_path = Path(tempfile.mkdtemp()) / "test_chore_box.db"
    chore_box = ChoreBox(db_path)
    core = Core(
        persona_text="人格", absolute_rules="ルール",
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        brains={}, chore_box=chore_box,
    )
    return core, chore_box


def test_sensitive_fragment_never_becomes_cloud_lane() -> None:
    core, chore_box = _core_with_chore_box()
    fragment = [
        Turn(speaker="master", text="俺の電話番号は090-1234-5678だよ"),
        Turn(speaker="serina", text="覚えておくね"),
    ]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "local", "電話番号を含む断片がcloudに漏れてはいけない"


def test_ordinary_fragment_uses_cloud_lane() -> None:
    core, chore_box = _core_with_chore_box()
    fragment = [
        Turn(speaker="master", text="今日は3時に駅で待ち合わせしよう"),
        Turn(speaker="serina", text="了解！"),
    ]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "cloud", "無害な断片はクラウドの速さを使ってよい"


def test_none_routing_rules_defaults_to_local() -> None:
    """routing_rules未設定（テスト等）なら安全側デフォルトを維持する"""
    db_path = Path(tempfile.mkdtemp()) / "test_chore_box2.db"
    chore_box = ChoreBox(db_path)
    core = Core(
        persona_text="人格", absolute_rules="ルール",
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=None,
        brains={}, chore_box=chore_box,
    )
    fragment = [Turn(speaker="master", text="今日は3時に駅で待ち合わせしよう")]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "local"


def main() -> None:
    tests = [
        test_sensitive_fragment_never_becomes_cloud_lane,
        test_ordinary_fragment_uses_cloud_lane,
        test_none_routing_rules_defaults_to_local,
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
