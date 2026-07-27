"""Pulse 発火判定の決定論テスト（合意台帳 §3.6）。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.idle_policy import (
    PulseConfig,
    ScheduleCandidate,
    collect_memory_pulse_candidates,
    decide_pulse,
    is_late_night,
    should_suppress_pulse,
)

CFG = PulseConfig(
    idle_before_seconds=2700,
    active_hour_start=8,
    active_hour_end=22,
    same_kind_gap_seconds=10800,
    emotion_gap_seconds=21600,
    min_interval_seconds=3600,
    late_night_start=23,
    late_night_end=7,
    mood_deviation_threshold=0.55,
)


def _local_at(hour: int, minute: int = 0) -> datetime:
    """ローカル暦の指定時刻（TZ 依存テストを安定化）。"""
    base = datetime.now().astimezone()
    return base.replace(hour=hour, minute=minute, second=0, microsecond=0)


NOW = _local_at(14)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def test_suppress_when_mute() -> None:
    reason = should_suppress_pulse(
        now=NOW, mute=True, conversation_active=False,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "mute"


def test_suppress_late_night() -> None:
    midnight = _local_at(2)
    assert is_late_night(now=midnight, late_night_start=23, late_night_end=7)
    reason = should_suppress_pulse(
        now=midnight, mute=False, conversation_active=False,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "late_night"


def test_suppress_during_conversation() -> None:
    reason = should_suppress_pulse(
        now=NOW, mute=False, conversation_active=True,
        last_pulse_at=None, config=CFG,
    )
    assert reason == "conversation_active"


def test_time_pulse_fires_after_idle() -> None:
    d = decide_pulse(
        now=NOW,
        last_activity_at=_ago(2800),
        mute=False,
        conversation_active=False,
        last_pulse_at=None,
        last_by_kind={},
        schedule_candidates=[],
        mood={"喜び": 0.1},
        config=CFG,
    )
    assert d.should_fire is True
    assert d.candidate is not None
    assert d.candidate.kind == "time"


def test_time_pulse_blocked_before_idle_threshold() -> None:
    d = decide_pulse(
        now=NOW,
        last_activity_at=_ago(1000),
        mute=False,
        conversation_active=False,
        last_pulse_at=None,
        last_by_kind={},
        schedule_candidates=[],
        mood={"喜び": 0.1},
        config=CFG,
    )
    assert d.should_fire is False


def test_memory_pulse_from_schedule_candidates() -> None:
    cand = ScheduleCandidate(
        fact_id="fact-hosp",
        window="直前",
        statement="15時に病院",
    )
    d = decide_pulse(
        now=NOW,
        last_activity_at=_ago(100),
        mute=False,
        conversation_active=False,
        last_pulse_at=None,
        last_by_kind={},
        schedule_candidates=[cand],
        mood={"喜び": 0.1},
        config=CFG,
    )
    assert d.should_fire is True
    assert d.candidate is not None
    assert d.candidate.kind == "memory"
    assert d.candidate.trigger_id == "fact-hosp:直前"
    assert d.candidate.context["window"] == "直前"


def test_collect_memory_pulse_passthrough_unfired_only() -> None:
    """Task 1-3側で除外済みなら collect は素通し（ここでは渡したものだけ返す）。"""
    cands = [
        ScheduleCandidate(fact_id="a", window="前夜", statement="明日病院"),
        ScheduleCandidate(fact_id="b", window="事後", statement="病院どうだった"),
    ]
    got = collect_memory_pulse_candidates(
        now=NOW,
        schedule_candidates=cands,
        last_by_kind={},
        config=CFG,
    )
    assert [c.trigger_id for c in got] == ["a:前夜", "b:事後"]


def test_build_schedule_candidates_skips_fired_and_orders_by_priority() -> None:
    from dataclasses import dataclass

    from serina.core.chores.idle_policy import build_schedule_candidates
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE
    from zoneinfo import ZoneInfo

    jst = ZoneInfo("Asia/Tokyo")
    now = datetime(2026, 7, 28, 14, 30, tzinfo=jst)  # 直前窓（15:00開始の1h前〜）

    @dataclass(frozen=True)
    class _F:
        id: str
        statement: str
        valid_from: str
        valid_to: str | None
        category: str

    facts = [
        _F(
            id="eve-only",
            statement="別件",
            valid_from="2026-07-30T10:00:00+09:00",
            valid_to=None,
            category=FACT_CATEGORY_SCHEDULE,
        ),
        _F(
            id="pre-hosp",
            statement="病院",
            valid_from="2026-07-28T15:00:00+09:00",
            valid_to="2026-07-28T17:00:00+09:00",
            category=FACT_CATEGORY_SCHEDULE,
        ),
        _F(
            id="pre-fired",
            statement="発火済み",
            valid_from="2026-07-28T15:30:00+09:00",
            valid_to=None,
            category=FACT_CATEGORY_SCHEDULE,
        ),
    ]
    state = {"fired": {"pre-fired:直前": now.isoformat()}}
    got = build_schedule_candidates(now=now, facts=facts, schedule_pulse_state=state)
    assert [c.fact_id for c in got] == ["pre-hosp"]
    assert got[0].window == "直前"
