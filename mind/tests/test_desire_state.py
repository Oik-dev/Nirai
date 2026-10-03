"""欲求層（DesireState）のテスト。実装計画 Phase 3 Task 3-1〜3-4。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.desire import (
    COMBINED_FACTOR_MIN,
    DEFAULT_BASE_RATE_PER_SECOND,
    DesireState,
    baseline_comfort_factor_from_baseline,
    circadian_factor_from_now,
    is_desire_gate_open,
    mood_factor_from_mood,
)

JST = ZoneInfo("Asia/Tokyo")


def test_desire_reaches_one_in_seven_days_at_neutral_factors() -> None:
    """Task 3-2: mood/baseline補正ゼロかつ circadian=0 の時刻なら7日で上限1.0。"""
    # JST hour=7 → circadian_factor = cos(2π*(7-1)/24) = 0
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 7, 0, tzinfo=JST)
    desire.tick(t0, 0.0, 0.0)
    desire.tick(t0 + timedelta(days=7), 0.0, 0.0)
    assert desire.level == 1.0


def test_desire_accumulates_when_left_idle() -> None:
    """Task 3-1/3-2: 放置すると level が上がる。"""
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 7, 0, tzinfo=JST)  # circadian=0
    desire.tick(t0, mood_factor=0.0, baseline_comfort_factor=0.0)
    desire.tick(
        t0 + timedelta(days=3.5),
        mood_factor=0.0,
        baseline_comfort_factor=0.0,
    )
    # 補正ゼロなら 3.5日で約0.5
    assert 0.45 <= desire.level <= 0.55


def test_mood_factor_changes_effective_rate() -> None:
    """Task 3-2: mood_factor 単独で速度が変わる。"""
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=JST)

    def _run(mood: float) -> float:
        d = DesireState()
        d.tick(t0, mood, 0.0)
        d.tick(t0 + timedelta(days=1), mood, 0.0)
        return d.level

    assert _run(1.0) > _run(0.0) > _run(-1.0)


def test_baseline_comfort_and_circadian_change_rate() -> None:
    """Task 3-2: baseline / circadian それぞれの単独変化で速度が変わる。"""
    t_peak = datetime(2026, 7, 1, 1, 0, tzinfo=JST)  # circadian=1
    t_valley = datetime(2026, 7, 1, 13, 0, tzinfo=JST)  # circadian=-1

    d_peak = DesireState()
    d_peak.tick(t_peak, 0.0, 0.0)
    d_peak.tick(t_peak + timedelta(days=1), 0.0, 0.0)

    d_valley = DesireState()
    d_valley.tick(t_valley, 0.0, 0.0)
    d_valley.tick(t_valley + timedelta(days=1), 0.0, 0.0)
    assert d_peak.level > d_valley.level

    d_hi = DesireState()
    d_hi.tick(t_valley, 0.0, 1.0)
    d_hi.tick(t_valley + timedelta(days=1), 0.0, 1.0)
    d_lo = DesireState()
    d_lo.tick(t_valley, 0.0, -1.0)
    d_lo.tick(t_valley + timedelta(days=1), 0.0, -1.0)
    assert d_hi.level > d_lo.level


def test_combined_factor_formula() -> None:
    """Task 3-2: 3項合成時の effective_rate。"""
    mood, base, circ = 1.0, 1.0, 1.0
    combined = 0.20 * mood + 0.10 * base + 0.10 * circ
    assert abs(combined - 0.40) < 1e-9
    rate = DEFAULT_BASE_RATE_PER_SECOND * (1.0 + combined)
    assert abs(rate - DEFAULT_BASE_RATE_PER_SECOND * 1.4) < 1e-12


def test_circadian_peaks_at_1am() -> None:
    assert abs(circadian_factor_from_now(datetime(2026, 7, 1, 1, 0)) - 1.0) < 1e-9
    assert abs(circadian_factor_from_now(datetime(2026, 7, 1, 13, 0)) - (-1.0)) < 1e-9


def test_circadian_converts_utc_to_jst() -> None:
    """Task 3-4: aware UTC は JST へ変換してから時刻を取る。"""
    # UTC 16:00 = JST 01:00 → 山頂
    utc_peak = datetime(2026, 7, 1, 16, 0, tzinfo=timezone.utc)
    assert abs(circadian_factor_from_now(utc_peak) - 1.0) < 1e-9
    # UTC 04:00 = JST 13:00 → 谷
    utc_valley = datetime(2026, 7, 1, 4, 0, tzinfo=timezone.utc)
    assert abs(circadian_factor_from_now(utc_valley) - (-1.0)) < 1e-9


def test_refractory_stops_accumulation() -> None:
    """Task 3-2/3-4: 不応期中は蓄積が止まる。"""
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    desire.tick(t0, 0.0, 0.0)
    desire.level = 0.8
    desire.discharge_and_enter_refractory(t0)
    assert desire.level == 0.05
    desire.tick(t0 + timedelta(hours=12), 1.0, 1.0)
    assert desire.level == 0.05  # 据え置き


def test_fulfillment_cycle_discharge_refractory_resume() -> None:
    """Task 3-4: 放電→不応→1.5日後に蓄積再開。"""
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    desire.tick(t0, 0.0, 0.0)
    desire.level = 0.7
    desire.discharge_and_enter_refractory(t0)
    assert desire.is_in_refractory(t0 + timedelta(days=1))
    # 不応中は減衰もしない
    desire.tick(t0 + timedelta(days=1), 0.0, 0.0, unfulfilled_decay=True)
    assert desire.level == 0.05
    # 1.5日経過後は蓄積再開
    after = t0 + timedelta(seconds=129_600)
    assert not desire.is_in_refractory(after)
    desire.tick(after, 0.0, 0.0)
    desire.tick(after + timedelta(days=1), 0.0, 0.0)
    assert desire.level > 0.05


def test_unfulfilled_slow_decay() -> None:
    """Task 3-4: 満たされない場合の緩やかな減衰（tau=30日、2026-07-30 レビューC-2是正）。"""
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    desire.tick(t0, 0.0, 0.0)
    desire.level = 0.8
    desire.tick(t0 + timedelta(days=30), 0.0, 0.0, unfulfilled_decay=True)
    assert desire.level < 0.8
    assert desire.level > 0.1  # 30日tauで一気に0にはならない


def test_unfulfilled_decay_slower_than_accumulation_near_threshold() -> None:
    """2026-07-30 レビューC-2是正a: level>=0.6域で減衰が蓄積より速くなり、
    頭打ちになる不具合の再発防止。同じ経過時間で「未充足減衰の下がり幅」が
    「最遅ケースの蓄積の伸び幅」を上回らないことを確認する。
    """
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    one_day = timedelta(days=1)

    decaying = DesireState()
    decaying.tick(t0, 0.0, 0.0)
    decaying.level = 0.6
    decaying.tick(t0 + one_day, 0.0, 0.0, unfulfilled_decay=True)
    decay_drop = 0.6 - decaying.level

    # combined_factorが理論最小(重みの合計から導出)の最遅ケースでの1日あたり蓄積量。
    # 2026-07-30 レビューM-1是正: ハードコード-0.40ではなくCOMBINED_FACTOR_MINを
    # 参照する。重み(MOOD_FACTOR_WEIGHT等)を変えてもこのテストが不変条件の番人になる。
    slowest_accumulation = (
        DEFAULT_BASE_RATE_PER_SECOND * (1.0 + COMBINED_FACTOR_MIN) * one_day.total_seconds()
    )

    assert decay_drop < slowest_accumulation


def test_level_before_tick_snapshots_pre_tick_value() -> None:
    """2026-07-30 レビューC-2是正b: level_before_tickはtickでlevelが動く前の値。"""
    desire = DesireState()
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    desire.tick(t0, 0.0, 0.0)  # 初回tickはlast_tick_atのみセット
    desire.level = 0.6
    desire.tick(t0 + timedelta(days=1), 0.0, 0.0, unfulfilled_decay=True)
    assert desire.level < 0.6  # 減衰でlevelは動いた
    assert desire.level_before_tick == 0.6  # スナップショットはtick前の値のまま


def test_desire_gate_closes_on_negative_affect() -> None:
    """Task 3-3: 負軸が閾値超ならゲート閉じ、未満なら開く。"""
    assert is_desire_gate_open({"嫌悪": 0.5, "怒り": 0.0, "悲しみ": 0.0, "恐れ": 0.0})
    assert not is_desire_gate_open({"嫌悪": 0.61, "怒り": 0.0, "悲しみ": 0.0, "恐れ": 0.0})
    assert not is_desire_gate_open({"嫌悪": 0.0, "怒り": 0.7, "悲しみ": 0.0, "恐れ": 0.0})
    assert not is_desire_gate_open({"嫌悪": 0.0, "怒り": 0.0, "悲しみ": 0.9, "恐れ": 0.0})
    assert not is_desire_gate_open({"嫌悪": 0.0, "怒り": 0.0, "悲しみ": 0.0, "恐れ": 0.6 + 1e-9})


def test_mood_and_baseline_factor_helpers() -> None:
    assert mood_factor_from_mood({"喜び": 1.0, "信頼": 1.0}) == 1.0
    assert mood_factor_from_mood({"喜び": 0.0, "信頼": 0.0}) == -1.0
    assert abs(mood_factor_from_mood({"喜び": 0.5, "信頼": 0.5})) < 1e-9
    assert baseline_comfort_factor_from_baseline({"喜び": 1.0, "信頼": 0.0}) == 0.0


def test_core_tick_desire_applies_unfulfilled_decay() -> None:
    """Task 3-4: Core._cool_emotion → _tick_desire が level 高時に減衰する。"""
    from mind.core.config import load_thresholds
    from mind.core.runtime import Core

    core = Core(persona_text="p", absolute_rules="r", thresholds=load_thresholds())
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    core.desire.last_tick_at = t0
    core.desire.level = 0.8
    core.emotion.last_tick_at = t0
    later = t0 + timedelta(days=3)
    core._cool_emotion(later)
    assert core.desire.level < 0.8
    assert core.desire.level > 0.2
    assert core.desire.last_tick_at == later
