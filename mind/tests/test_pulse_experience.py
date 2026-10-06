"""段階3 S7: 話しかけたあとの反応から、自分のペースを決める。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from mind.core.chores.pulse_experience import connection_gap_seconds, unanswered_connection_streak
from mind.core.lifelog import Line, MASTER, PulseLog


BASE = datetime(2026, 10, 6, 0, 0, tzinfo=timezone.utc)


def _pulse(at: datetime) -> dict:
    return {"ts": at.isoformat(), "kind": "connection", "trigger_id": "connection"}


def _master(at: datetime) -> Line:
    return Line(day_file="2026-10-06", no=1, ts=at, session="s", speaker=MASTER, text="返事")


def test_connection_gap_grows_3_6_12_24_hours_without_fast_reply() -> None:
    entries = [_pulse(BASE), _pulse(BASE + timedelta(hours=6)), _pulse(BASE + timedelta(hours=18))]
    assert unanswered_connection_streak(entries[:1], []) == 1
    assert connection_gap_seconds(entries[:1], [], base_seconds=3 * 3600) == 6 * 3600
    assert connection_gap_seconds(entries[:2], [], base_seconds=3 * 3600) == 12 * 3600
    assert connection_gap_seconds(entries, [], base_seconds=3 * 3600) == 24 * 3600
    assert connection_gap_seconds(entries + [_pulse(BASE + timedelta(hours=42))], [], base_seconds=3 * 3600) == 24 * 3600


def test_fast_reply_resets_gap_but_slow_reply_counts_as_missed() -> None:
    fired = BASE
    entries = [_pulse(fired)]
    assert connection_gap_seconds(entries, [_master(fired + timedelta(minutes=59))], base_seconds=3 * 3600) == 3 * 3600
    assert connection_gap_seconds(entries, [_master(fired + timedelta(minutes=61))], base_seconds=3 * 3600) == 6 * 3600


def test_first_master_reply_before_next_connection_is_the_response() -> None:
    first = BASE
    second = BASE + timedelta(hours=6)
    entries = [_pulse(first), _pulse(second)]
    conversation = [
        _master(first + timedelta(hours=2)),
        _master(second + timedelta(minutes=30)),
    ]
    assert unanswered_connection_streak(entries, conversation) == 0


def test_pulse_log_is_append_only_and_readable(tmp_path: Path) -> None:
    log = PulseLog(tmp_path / "pulse")
    log.append(ts=BASE, kind="wake", trigger_id="wake:1")
    log.append(ts=BASE + timedelta(hours=1), kind="connection", trigger_id="connection")
    rows = log.entries()
    assert [(row["kind"], row["trigger_id"]) for row in rows] == [("wake", "wake:1"), ("connection", "connection")]
    assert [row["ts"] for row in rows] == [BASE.isoformat(), (BASE + timedelta(hours=1)).isoformat()]
