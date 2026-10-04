"""能動 Pulse の発火判定（決定論）。設計書 §2.8。

タイマ・スレッド・ロックといった実行の都合から切り離した純粋関数にする（スレッドループに判定を埋めると
sleep依存でテストできない）。呼び出し側（app/gui_server.pyの見回りスレッド）が「起こす・判定を呼ぶ・実行する」だけを担う。
文面は Brain が書く（core/chores/pulse.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


# --- Pulse（合意台帳 §3.6 / Wave 6）---


@dataclass(frozen=True)
class PulseConfig:
    """Pulse 発火判定の設定値（config/thresholds.toml [pulse]）。"""

    idle_before_seconds: float
    active_hour_start: int
    active_hour_end: int
    same_kind_gap_seconds: float
    emotion_gap_seconds: float
    min_interval_seconds: float
    late_night_start: int
    late_night_end: int
    mood_deviation_threshold: float


@dataclass(frozen=True)
class PulseCandidate:
    kind: str  # "wake" | "emotion" | "time"
    trigger_id: str
    context: dict[str, Any]


@dataclass(frozen=True)
class PulseDecision:
    should_fire: bool
    candidate: PulseCandidate | None = None
    suppressed_reason: str | None = None


def is_active_hours(*, now: datetime, active_hour_start: int, active_hour_end: int) -> bool:
    """活動時間帯（ローカル時刻）内か。"""
    hour = now.astimezone().hour
    if active_hour_start <= active_hour_end:
        return active_hour_start <= hour < active_hour_end
    return hour >= active_hour_start or hour < active_hour_end


def is_late_night(*, now: datetime, late_night_start: int, late_night_end: int) -> bool:
    """深夜帯（ローカル時刻）。active_hours の逆ではなく独立設定。"""
    hour = now.astimezone().hour
    if late_night_start <= late_night_end:
        return late_night_start <= hour < late_night_end
    return hour >= late_night_start or hour < late_night_end


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(ts)


def _seconds_since(ts: str | None, now: datetime) -> float | None:
    parsed = _parse_iso(ts)
    if parsed is None:
        return None
    return (now - parsed).total_seconds()


def should_suppress_pulse(
    *,
    now: datetime,
    mute: bool,
    conversation_active: bool,
    last_pulse_at: str | None,
    config: PulseConfig,
) -> str | None:
    """沈黙優先条件。抑制理由文字列を返す（抑制なしなら None）。"""
    if mute:
        return "mute"
    if conversation_active:
        return "conversation_active"
    if is_late_night(
        now=now,
        late_night_start=config.late_night_start,
        late_night_end=config.late_night_end,
    ):
        return "late_night"
    elapsed = _seconds_since(last_pulse_at, now)
    if elapsed is not None and elapsed < config.min_interval_seconds:
        return "min_interval"
    return None


def _kind_gap_ok(
    *,
    kind: str,
    now: datetime,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> bool:
    last = last_by_kind.get(kind)
    elapsed = _seconds_since(last, now)
    if elapsed is None:
        return True
    gap = config.emotion_gap_seconds if kind == "emotion" else config.same_kind_gap_seconds
    return elapsed >= gap


def collect_time_pulse_candidate(
    *,
    now: datetime,
    last_activity_at: datetime,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    if (now - last_activity_at).total_seconds() < config.idle_before_seconds:
        return None
    if not _kind_gap_ok(kind="time", now=now, last_by_kind=last_by_kind, config=config):
        return None
    idle_min = int((now - last_activity_at).total_seconds() // 60)
    return PulseCandidate(
        kind="time",
        trigger_id=f"time-{now.astimezone().date().isoformat()}",
        context={"idle_minutes": idle_min, "reason": "idle_timeout"},
    )


def collect_emotion_pulse_candidate(
    *,
    now: datetime,
    mood: dict[str, float],
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    if not _kind_gap_ok(kind="emotion", now=now, last_by_kind=last_by_kind, config=config):
        return None
    peak_axis = max(mood, key=mood.get, default="")
    peak_value = mood.get(peak_axis, 0.0)
    if peak_value < config.mood_deviation_threshold:
        return None
    return PulseCandidate(
        kind="emotion",
        trigger_id=f"emotion-{peak_axis}",
        context={
            "dominant_axis": peak_axis,
            "dominant_value": peak_value,
            "reason": "sustained_mood",
        },
    )


def collect_waking_pulse_candidate(
    *,
    now: datetime,
    last_activity_at: datetime | None,
    woke_at: datetime | None,
    tell: str,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    """目覚めて伝えたくなったことがあり、マスターがまだ来ていなければ、本人から話しかけに行く（core/memory/waking.py）。

    last_activity_at はマスターが最後に話しかけた時刻（起動してからまだなら None＝まだ来ていない）。
    目覚めてからマスターが話しかけていれば、伝えたいことは会話の手元（文脈パックの【今の自分】）にあるので、行かない。
    1回の目覚めで行くのは1度だけ。
    """
    if woke_at is None or not tell:
        return None
    if last_activity_at is not None and last_activity_at >= woke_at:
        return None
    told = _parse_iso(last_by_kind.get("wake"))
    if told is not None and told >= woke_at:
        return None
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    return PulseCandidate(
        kind="wake",
        trigger_id=f"wake-{woke_at.isoformat()}",
        context={"reason": "waking_thought", "thought": tell},
    )


def decide_pulse(
    *,
    now: datetime,
    last_activity_at: datetime | None,
    mute: bool,
    conversation_active: bool,
    last_pulse_at: str | None,
    last_by_kind: dict[str, str],
    mood: dict[str, float],
    config: PulseConfig,
    woke_at: datetime | None = None,
    tell: str = "",
) -> PulseDecision:
    """Pulse 発火判定（決定論）。文面生成は呼び出し側が Brain へ委譲する。

    last_activity_at はマスターが最後に話しかけた時刻。起動してからまだ来ていなければ None で、
    そのあいだ暇や気分では話しかけない（起動直後に勝手に来ない）。目覚めて伝えたいことは別。
    """
    suppressed = should_suppress_pulse(
        now=now,
        mute=mute,
        conversation_active=conversation_active,
        last_pulse_at=last_pulse_at,
        config=config,
    )
    if suppressed:
        return PulseDecision(should_fire=False, suppressed_reason=suppressed)

    candidates: list[PulseCandidate] = []
    wake = collect_waking_pulse_candidate(
        now=now,
        last_activity_at=last_activity_at,
        woke_at=woke_at,
        tell=tell,
        last_by_kind=last_by_kind,
        config=config,
    )
    if wake:
        candidates.append(wake)
    if last_activity_at is None:
        return PulseDecision(should_fire=wake is not None, candidate=wake)
    emo = collect_emotion_pulse_candidate(
        now=now, mood=mood, last_by_kind=last_by_kind, config=config,
    )
    if emo:
        candidates.append(emo)
    tim = collect_time_pulse_candidate(
        now=now,
        last_activity_at=last_activity_at,
        last_by_kind=last_by_kind,
        config=config,
    )
    if tim:
        candidates.append(tim)

    if not candidates:
        return PulseDecision(should_fire=False)

    # 優先: 目覚めて伝えたいこと → emotion → time
    priority = {"wake": 0, "emotion": 1, "time": 2}
    chosen = min(candidates, key=lambda c: priority.get(c.kind, 99))
    return PulseDecision(should_fire=True, candidate=chosen)
