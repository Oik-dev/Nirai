"""感情状態（プルチック8軸×情動/気分二層）のテスト。設計書v2 §2.3, §2.6"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.state.emotion import PLUTCHIK_AXES, EmotionState


def test_initial_state_is_neutral() -> None:
    state = EmotionState()
    for axis in PLUTCHIK_AXES:
        assert state.affect[axis] == 0.0
        assert state.mood[axis] == 0.0


def test_affect_can_swing_fully_in_one_turn() -> None:
    """情動層は1ターンで振り切ってよい（変化制限なし）"""
    state = EmotionState()
    state.apply_affect_delta({"怒り": 1.0})
    assert state.affect["怒り"] == 1.0


def test_affect_is_clamped_to_valid_range() -> None:
    state = EmotionState()
    state.apply_affect_delta({"怒り": 5.0})
    assert state.affect["怒り"] == 1.0
    state.apply_affect_delta({"怒り": -10.0})
    assert state.affect["怒り"] == 0.0


def test_mood_change_is_limited_by_guard() -> None:
    """気分層は急変防止弁により1ターンの変化幅が制限される（§2.3）"""
    state = EmotionState()
    state.apply_mood_delta({"喜び": 1.0}, max_delta_per_turn=0.1)
    assert state.mood["喜び"] == 0.1


def test_mood_change_within_guard_applies_fully() -> None:
    state = EmotionState()
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    assert state.mood["喜び"] == 0.05


def test_unknown_axis_is_rejected() -> None:
    state = EmotionState()
    try:
        state.apply_affect_delta({"謎の感情": 0.5})
        raise AssertionError("未知の軸が受理されてしまった")
    except KeyError:
        pass


def main() -> None:
    tests = [
        test_initial_state_is_neutral,
        test_affect_can_swing_fully_in_one_turn,
        test_affect_is_clamped_to_valid_range,
        test_mood_change_is_limited_by_guard,
        test_mood_change_within_guard_applies_fully,
        test_unknown_axis_is_rejected,
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
