"""感情状態（プルチック8軸×情動/気分二層）のテスト。設計書 §2.3, §2.6"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.state.emotion import PLUTCHIK_AXES, EmotionState


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


def test_unknown_axis_is_ignored() -> None:
    """未知軸は無視し、既知軸の更新は続行する（Brain幻覚でターンを落とさない）。"""
    state = EmotionState()
    state.apply_affect_delta({"謎の感情": 0.5, "喜び": 0.3})
    assert state.affect["喜び"] == 0.3
    state.apply_mood_delta({"謎の感情": 1.0, "信頼": 0.2}, max_delta_per_turn=0.1)
    assert state.mood["信頼"] == 0.1
    assert len(state.mood_trajectory) == 1


def test_unknown_only_mood_delta_does_not_append_trajectory() -> None:
    state = EmotionState()
    state.apply_mood_delta({"謎の感情": 1.0}, max_delta_per_turn=0.1)
    assert state.mood_trajectory == []


def test_apply_time_cooling_moves_toward_baseline() -> None:
    from datetime import datetime, timedelta, timezone

    state = EmotionState()
    t0 = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    state.apply_time_cooling(
        t0, tau_affect_seconds=3600, tau_mood_seconds=86400, baselines={"怒り": 0.0},
    )
    state.apply_affect_delta({"怒り": 1.0})
    assert state.affect["怒り"] == 1.0
    state.apply_time_cooling(
        t0 + timedelta(hours=2),
        tau_affect_seconds=3600,
        tau_mood_seconds=86400,
        baselines={"怒り": 0.0},
    )
    assert state.affect["怒り"] < 0.2
    cooled = state.affect["怒り"]
    state.apply_time_cooling(
        t0 + timedelta(hours=2),
        tau_affect_seconds=3600,
        tau_mood_seconds=86400,
        baselines={"怒り": 0.0},
    )
    assert state.affect["怒り"] == cooled


def test_trajectory_snapshot_tagged_with_current_day() -> None:
    """2026-07-26 A3: current_dayが軌跡スナップショットに_dayとして載る。"""
    state = EmotionState()
    state.current_day = "2026-07-25"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    assert state.mood_trajectory[-1]["_day"] == "2026-07-25"


def test_summarize_trajectory_filters_by_day() -> None:
    state = EmotionState()
    state.current_day = "2026-07-25"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    state.current_day = "2026-07-26"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)

    summary_25 = state.summarize_trajectory(day="2026-07-25")
    summary_26 = state.summarize_trajectory(day="2026-07-26")
    summary_all = state.summarize_trajectory()

    assert "喜び" in summary_25
    assert "喜び" in summary_26
    assert "喜び" in summary_all
    # 07-25は1件だけ追記→開始と終了が同じ値
    assert "開始0.05→終了0.05" in summary_25
    # 07-26は2件追記（moodは日をまたいでも累積継続）→0.10から0.15へ
    assert "開始0.10→終了0.15" in summary_26


def test_summarize_trajectory_unknown_day_is_empty() -> None:
    state = EmotionState()
    state.current_day = "2026-07-25"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    assert state.summarize_trajectory(day="2026-07-01") == ""


def test_clear_trajectory_removes_only_matching_day() -> None:
    """2026-07-26 A3: clear_trajectory(day=...)は該当日だけを消し、他日は残す。"""
    state = EmotionState()
    state.current_day = "2026-07-25"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    state.current_day = "2026-07-26"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)

    state.clear_trajectory(day="2026-07-25")

    assert state.summarize_trajectory(day="2026-07-25") == ""
    assert state.summarize_trajectory(day="2026-07-26") != ""
    assert len(state.mood_trajectory) == 1


def test_clear_trajectory_none_clears_all() -> None:
    state = EmotionState()
    state.current_day = "2026-07-25"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)
    state.current_day = "2026-07-26"
    state.apply_mood_delta({"喜び": 0.05}, max_delta_per_turn=0.1)

    state.clear_trajectory()

    assert state.mood_trajectory == []


def test_opposite_coupling_pulls_opposite_axis_down() -> None:
    """2026-07-26 A5: 喜び+0.4のみ報告→悲しみが対極カップリングで減る。"""
    state = EmotionState()
    state.affect["悲しみ"] = 0.5
    state.apply_affect_delta({"喜び": 0.4}, opposite_coupling_ratio=0.5)
    assert state.affect["喜び"] == 0.4
    assert round(state.affect["悲しみ"], 10) == 0.3  # 0.5 - 0.4*0.5


def test_opposite_coupling_skipped_when_opposite_axis_is_explicit() -> None:
    """喜び+0.4と悲しみ+0.1を同時報告→悲しみは明示値どおり（カップリング不適用）。"""
    state = EmotionState()
    state.affect["悲しみ"] = 0.5
    state.apply_affect_delta({"喜び": 0.4, "悲しみ": 0.1}, opposite_coupling_ratio=0.5)
    assert state.affect["喜び"] == 0.4
    assert round(state.affect["悲しみ"], 10) == 0.6  # 0.5 + 0.1、カップリングは適用されない


def test_negative_delta_does_not_trigger_opposite_coupling() -> None:
    """悲しみ-0.3のみ報告→喜びは動かない（幽霊感情の防止）。"""
    state = EmotionState()
    state.affect["悲しみ"] = 0.5
    state.apply_affect_delta({"悲しみ": -0.3}, opposite_coupling_ratio=0.5)
    assert round(state.affect["悲しみ"], 10) == 0.2
    assert state.affect["喜び"] == 0.0


def test_opposite_coupling_ratio_zero_matches_legacy_behavior() -> None:
    """opposite_coupling_ratio=0.0で従来と同一挙動（対極軸は動かない）。"""
    state = EmotionState()
    state.affect["悲しみ"] = 0.5
    state.apply_affect_delta({"喜び": 0.4}, opposite_coupling_ratio=0.0)
    assert state.affect["喜び"] == 0.4
    assert state.affect["悲しみ"] == 0.5

    state_default = EmotionState()
    state_default.affect["悲しみ"] = 0.5
    state_default.apply_affect_delta({"喜び": 0.4})  # 既定値も0.0
    assert state_default.affect["喜び"] == 0.4
    assert state_default.affect["悲しみ"] == 0.5


def test_apply_mood_bleed_moves_mood_toward_affect() -> None:
    """2026-07-26 A6: 情動0.8・気分0.0の状態で1ターン→気分が0.8*0.08=0.064増える。"""
    state = EmotionState()
    state.affect["喜び"] = 0.8
    state.apply_mood_bleed(bleed_rate=0.08, max_delta_per_turn=0.1)
    assert round(state.mood["喜び"], 10) == 0.064


def test_apply_mood_bleed_converges_without_overshoot() -> None:
    """同じ情動が10ターン続く→気分が単調増加し0.8へ漸近する（超えない）。"""
    state = EmotionState()
    state.affect["喜び"] = 0.8
    prev = 0.0
    for _ in range(10):
        state.apply_mood_bleed(bleed_rate=0.08, max_delta_per_turn=0.1)
        assert state.mood["喜び"] > prev
        assert state.mood["喜び"] <= 0.8
        prev = state.mood["喜び"]


def test_apply_mood_bleed_moves_mood_even_with_no_new_affect_delta() -> None:
    """付箋0枚のターンでも気分は情動へ寄る（毎ターン呼ぶ前提のため）。"""
    state = EmotionState()
    state.affect["信頼"] = 0.5
    state.apply_mood_bleed(bleed_rate=0.1, max_delta_per_turn=0.5)
    assert state.mood["信頼"] > 0.0


def test_apply_mood_bleed_respects_max_delta_per_turn() -> None:
    """にじみ量がmax_delta_per_turnを超えない（bleed_rateを大きくして確認）。"""
    state = EmotionState()
    state.affect["怒り"] = 1.0
    state.apply_mood_bleed(bleed_rate=1.0, max_delta_per_turn=0.1)
    assert round(state.mood["怒り"], 10) == 0.1  # 本来は1.0*1.0=1.0だが上限0.1でクランプ


def main() -> None:
    tests = [
        test_initial_state_is_neutral,
        test_affect_can_swing_fully_in_one_turn,
        test_affect_is_clamped_to_valid_range,
        test_mood_change_is_limited_by_guard,
        test_mood_change_within_guard_applies_fully,
        test_unknown_axis_is_ignored,
        test_unknown_only_mood_delta_does_not_append_trajectory,
        test_apply_time_cooling_moves_toward_baseline,
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
