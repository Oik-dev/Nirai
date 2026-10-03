"""Pulse 発火判定の決定論テスト（合意台帳 §3.6）。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.idle_policy import (
    PulseConfig,
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
        mood={"喜び": 0.1},
        config=CFG,
    )
    assert d.should_fire is False


