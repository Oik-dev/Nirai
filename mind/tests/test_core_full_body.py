"""Core全身検査: 台本通りに返す人形(StubBrain)で会話→付箋→状態更新の全フローを検査する。

設計書 §5.2「Core全身検査」。LLM不要・記憶接続なし（Phase2で接続）。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig
from mind.core.runtime import Core

# 予定即時書き込みテスト用の固定時刻（年依存を避ける I-5）
_SCHEDULE_NOW = datetime(2026, 7, 27, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))


class StubBrain:
    """台本通りに返す人形。設計書 §5.2。"""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.received_pack = None

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.received_pack = pack
        return self.script


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
    )


def test_turn_updates_state_and_records_session() -> None:
    core = Core(persona_text="価値観: 誠実", absolute_rules="機微を漏らさない", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "おかえりなさい",
        "fusen_list": [
            {
                "kind": "心の動き",
                "version": 1,
                "content": {"deltas": {"喜び": 0.4}, "trigger": "帰宅の挨拶"},
                "confidence": 0.9,
            }
        ],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    result = core.turn("ただいま", brain)

    assert result.report.reply == "おかえりなさい"
    assert core.emotion.affect["喜び"] == 0.4
    assert len(core.session.turns) == 2
    assert core.session.turns[0].text == "ただいま"
    assert core.session.turns[1].text == "おかえりなさい"
    # 2026-07-26 Minor是正: 非routed経路でも軌跡タグ・観測時刻を付与する
    assert core.emotion.current_day is not None
    assert core.relationship.current_turn_at is not None
    assert any(
        snap.get("_day") == core.emotion.current_day
        for snap in core.emotion.mood_trajectory
    )


def test_turn_tags_the_mood_trajectory_with_the_serina_day() -> None:
    """気分の流れは Serina 日（朝7時が境目）で日を付ける。眠りの間の日記がその日の分を読む。"""
    from datetime import timezone

    jst = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 7, 26, 5, 30, tzinfo=jst).astimezone(timezone.utc)  # 朝7時前は前の日
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "了解",
        "fusen_list": [{
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.2}, "trigger": "t"},
            "confidence": 0.9,
        }],
        "self_assessment": {"over_capacity": False, "reason": "x"},
    })
    core.turn("やあ", brain, now=now)
    assert core.emotion.current_day == "2026-07-25"


def test_brain_receives_context_pack_with_master_utterance() -> None:
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "こんにちは",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("やあ", brain)

    assert brain.received_pack is not None
    assert "やあ" in brain.received_pack.render()


def test_second_turn_sees_first_turn_in_recent_history() -> None:
    """Brainはステートレスだが、Coreが毎ターン文脈パックに履歴を積み直す（条文A）"""
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "了解です",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("最初の話題", brain)
    core.turn("次の話題", brain)

    assert "最初の話題" in brain.received_pack.render()
    assert "次の話題" in brain.received_pack.render()


def main() -> None:
    tests = [
        test_turn_updates_state_and_records_session,
        test_turn_tags_the_mood_trajectory_with_the_serina_day,
        test_brain_receives_context_pack_with_master_utterance,
        test_second_turn_sees_first_turn_in_recent_history,
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
