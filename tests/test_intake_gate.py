"""Core intake: 関所①②③＋状態更新（§2.5, §2.7）。Phase1は④引用照合・記憶審査を含まない"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import ThresholdsConfig
from serina.core.intake.gate import process_report
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "心の動き": 0.5, "マスター観測": 0.5},
        mood_guard_max_delta_per_turn=0.1,
    )


def _raw_report(fusen_list: list[dict]) -> dict:
    return {
        "reply": "そうだったんですね",
        "fusen_list": fusen_list,
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    }


def test_accepted_fusen_updates_affect_and_guarded_mood() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.6}, "trigger": "褒められた"},
            "confidence": 0.9,
        }
    ])

    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert emotion.affect["喜び"] == 0.6
    assert emotion.mood["喜び"] == 0.1  # 急変防止弁で0.1にクランプ
    assert len(result.accepted_fusen) == 1
    assert result.rejected_by_confidence == []
    assert result.discarded_by_format == []


def test_low_confidence_fusen_is_rejected_and_state_untouched() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"怒り": 0.5}, "trigger": "些細な違和感"},
            "confidence": 0.2,  # 閾値0.5未満
        }
    ])

    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert emotion.affect["怒り"] == 0.0
    assert len(result.rejected_by_confidence) == 1
    assert result.accepted_fusen == []


def test_master_observation_updates_relationship() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "マスター観測",
            "version": 1,
            "content": {"observation": "疲れてそう"},
            "confidence": 0.8,
        }
    ])

    process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert relationship.recent_master_mood == "疲れてそう"


def test_broken_fusen_is_discarded_and_reported() -> None:
    emotion = EmotionState()
    relationship = RelationshipState()
    raw = _raw_report([
        {
            "kind": "マスター観測",
            "version": 1,
            "content": {"observation": "機嫌が良い"},
            "confidence": 3.0,  # 書式違反(範囲外)
        }
    ])

    result = process_report(raw, emotion=emotion, relationship=relationship, thresholds=_thresholds())

    assert len(result.discarded_by_format) == 1
    assert relationship.recent_master_mood is None


def main() -> None:
    tests = [
        test_accepted_fusen_updates_affect_and_guarded_mood,
        test_low_confidence_fusen_is_rejected_and_state_untouched,
        test_master_observation_updates_relationship,
        test_broken_fusen_is_discarded_and_reported,
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
