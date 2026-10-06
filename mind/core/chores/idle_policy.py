"""能動 Pulse の発火判定（決定論）。設計書 §2.8。

タイマ・スレッド・ロックといった実行の都合から切り離した純粋関数にする（スレッドループに判定を埋めると
sleep依存でテストできない）。呼び出し側（app/gui_server.pyの見回りスレッド）が「起こす・判定を呼ぶ・実行する」だけを担う。
文面は Brain が書く（core/chores/pulse.py）。

本人から話しかけに行くわけは3つ（この順に先）。
- 目覚め（wake）：目覚めて伝えたくなったことがあり、目覚めてからマスターがまだ話しかけていない。
- その日（day）：約束や予定・記念日の日が来て（core/memory/relation.py）、今日はまだマスターが来ておらず、本人もまだ話しかけに
  行っていない（今日もう行ったなら、その日のことは材料の【マスターとのこと】に入っていた）。
- つながり（connection）：会えない時間でつながりが減って、人恋しい（core/feeling/。いつ人恋しくなるかは気持ちが決める）。
来てよい時間帯・深夜・mute・会話中・間隔の決まりは、どれにも同じにかかる安全柵（本人が経験からペースを決めるようになるまで）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from mind.core.memory.structure import JST


@dataclass(frozen=True)
class PulseConfig:
    """Pulse 発火判定の設定値（config/thresholds.toml [pulse]）。"""

    active_hour_start: int
    active_hour_end: int
    same_kind_gap_seconds: float
    min_interval_seconds: float
    late_night_start: int
    late_night_end: int


@dataclass(frozen=True)
class PulseCandidate:
    kind: str  # "wake" | "day" | "connection"
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


def _span(seconds: float) -> str:
    """どれだけ会っていないか（文面の材料）。"""
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))}分"
    if seconds < 86400:
        return f"{int(seconds // 3600)}時間"
    return f"{int(seconds // 86400)}日"


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
    elapsed = _seconds_since(last_by_kind.get(kind), now)
    return elapsed is None or elapsed >= config.same_kind_gap_seconds


def collect_connection_pulse_candidate(
    *,
    now: datetime,
    lonely: bool,
    master_spoke_at: datetime | None,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    """人恋しければ、本人から会いに行く。起動してからマスターがまだ来ていなくても行く（つながりは記録から分かる）。"""
    if not lonely:
        return None
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    if not _kind_gap_ok(kind="connection", now=now, last_by_kind=last_by_kind, config=config):
        return None
    context: dict[str, Any] = {"reason": "missing_master"}
    if master_spoke_at is not None:
        context["since_master_spoke"] = _span((now - master_spoke_at).total_seconds())
    return PulseCandidate(
        kind="connection",
        trigger_id=f"connection-{now.astimezone().date().isoformat()}",
        context=context,
    )


def collect_day_pulse_candidate(
    *,
    now: datetime,
    due: tuple[str, ...],
    master_spoke_at: datetime | None,
    last_pulse_at: str | None,
    config: PulseConfig,
) -> PulseCandidate | None:
    """今日が約束や予定・記念日の日（due はその書き留め）で、今日まだマスターが来ておらず、本人も今日まだ話しかけて
    いなければ、本人から話しかけに行く。1日に1度だけ（今日の日付はどの Pulse の材料にも入っている）。"""
    if not due:
        return None
    today_start = now.astimezone(JST).replace(hour=0, minute=0, second=0, microsecond=0)  # 今日は日本時間の暦の日
    if master_spoke_at is not None and master_spoke_at >= today_start:
        return None
    went = _parse_iso(last_pulse_at)
    if went is not None and went >= today_start:
        return None
    if not is_active_hours(
        now=now,
        active_hour_start=config.active_hour_start,
        active_hour_end=config.active_hour_end,
    ):
        return None
    return PulseCandidate(
        kind="day",
        trigger_id=f"day-{today_start.date().isoformat()}",
        context={"reason": "the_day", "today": list(due)},
    )


def collect_waking_pulse_candidate(
    *,
    now: datetime,
    master_spoke_at: datetime | None,
    woke_at: datetime | None,
    tell: str,
    last_by_kind: dict[str, str],
    config: PulseConfig,
) -> PulseCandidate | None:
    """目覚めて伝えたくなったことがあり、マスターがまだ来ていなければ、本人から話しかけに行く（core/memory/waking.py）。

    master_spoke_at は帳簿にあるマスターの最後の発言の時刻（再起動をまたいでも失わない）。
    目覚めてからマスターが話しかけていれば、伝えたいことは会話の手元（文脈パックの【今の自分】）にあるので、行かない。
    1回の目覚めで行くのは1度だけ。
    """
    if woke_at is None or not tell:
        return None
    if master_spoke_at is not None and master_spoke_at >= woke_at:
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
    mute: bool,
    conversation_active: bool,
    last_pulse_at: str | None,
    last_by_kind: dict[str, str],
    config: PulseConfig,
    lonely: bool = False,
    woke_at: datetime | None = None,
    tell: str = "",
    master_spoke_at: datetime | None = None,
    due_today: tuple[str, ...] = (),
) -> PulseDecision:
    """Pulse 発火判定（決定論）。文面生成は呼び出し側が Brain へ委譲する。

    lonely は今人恋しいか（core/feeling/feelings.py）。master_spoke_at は帳簿にあるマスターの最後の発言の時刻。
    due_today は今日がその日の約束や予定・記念日（core/memory/relation.py の due_today）。
    conversation_active は会話中か（会話が途切れたかの判定は呼び出し側。眠りと同じく、最後の発言から少しあける）。
    抑えられた候補は、行ったことにならない（次の見回りでもう一度判定する）。
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

    wake = collect_waking_pulse_candidate(
        now=now,
        master_spoke_at=master_spoke_at,
        woke_at=woke_at,
        tell=tell,
        last_by_kind=last_by_kind,
        config=config,
    )
    if wake is not None:  # 目覚めて伝えたいことが先
        return PulseDecision(should_fire=True, candidate=wake)
    day = collect_day_pulse_candidate(
        now=now, due=due_today, master_spoke_at=master_spoke_at, last_pulse_at=last_pulse_at, config=config,
    )
    if day is not None:
        return PulseDecision(should_fire=True, candidate=day)
    connection = collect_connection_pulse_candidate(
        now=now,
        lonely=lonely,
        master_spoke_at=master_spoke_at,
        last_by_kind=last_by_kind,
        config=config,
    )
    return PulseDecision(should_fire=connection is not None, candidate=connection)
