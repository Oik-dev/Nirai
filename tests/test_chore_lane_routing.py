"""裏方便の断片ごとの車線振り分けテスト。設計書 §2.4/§2.6。

2026-07-18: §9.3で裏方便のcloud車線は永久退役。会話文・その要約をクラウドへ送らない
確定方針（議題2.5）のため、機微判定に関わらずすべての断片がlane="local"固定になる
（旧: センシティブな断片のみlocal・無害な断片はcloud、という振り分けは廃止）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.runtime import Core
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.session import Turn


def _registry() -> list[BrainEntry]:
    return [BrainEntry("serina-gemma4-unc", "ollama", "local", "primary", -1, -1, "small")]


def _core_with_chore_box(routing_rules: RoutingRules | None = RoutingRules()) -> tuple[Core, ChoreBox]:
    db_path = Path(tempfile.mkdtemp()) / "test_chore_box.db"
    chore_box = ChoreBox(db_path)
    core = Core(
        persona_text="人格", absolute_rules="ルール",
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        registry=_registry(), quota_ledger=QuotaLedger(), routing_rules=routing_rules,
        brains={}, chore_box=chore_box,
    )
    return core, chore_box


def test_sensitive_fragment_becomes_local_lane() -> None:
    core, chore_box = _core_with_chore_box()
    fragment = [
        Turn(speaker="master", text="俺の電話番号は090-1234-5678だよ"),
        Turn(speaker="serina", text="覚えておくね"),
    ]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "local", "電話番号を含む断片がcloudに漏れてはいけない"


def test_ordinary_fragment_also_becomes_local_lane() -> None:
    """§9.3: cloud車線は永久退役。無害な断片であっても常にlocal固定（回帰テスト）。"""
    core, chore_box = _core_with_chore_box()
    fragment = [
        Turn(speaker="master", text="今日は3時に駅で待ち合わせしよう"),
        Turn(speaker="serina", text="了解！"),
    ]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "local", "cloud車線は退役済み。無害な断片でもlocal固定であるべき"


def test_none_routing_rules_defaults_to_local() -> None:
    """routing_rules未設定（テスト等）でも安全側デフォルト(local)を維持する"""
    core, chore_box = _core_with_chore_box(routing_rules=None)
    fragment = [Turn(speaker="master", text="今日は3時に駅で待ち合わせしよう")]

    job_id = core._enqueue_chore_fragment(fragment)

    job = next(j for j in chore_box.pending() if j.id == job_id)
    assert job.lane == "local"


def main() -> None:
    tests = [
        test_sensitive_fragment_becomes_local_lane,
        test_ordinary_fragment_also_becomes_local_lane,
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
