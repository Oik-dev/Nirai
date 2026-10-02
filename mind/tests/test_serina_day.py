"""Serina 日（07:00 境）ヘルパのテスト。設計書 §2.4。"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.serina_day import (
    crossed_serina_day_boundary,
    is_serina_day_boundary_instant,
    serina_day_id,
    should_run_day_boundary,
)

JST = ZoneInfo("Asia/Tokyo")


def _local_dt(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=JST).astimezone(timezone.utc)


def test_serina_day_id_before_boundary_is_previous_day() -> None:
    # 2026-07-22 06:59 JST → Serina 日 2026-07-21
    dt = _local_dt(2026, 7, 22, 6, 59)
    assert serina_day_id(dt, boundary_hour=7) == date(2026, 7, 21)


def test_serina_day_id_at_boundary_is_current_day() -> None:
    # 2026-07-22 07:00 JST → Serina 日 2026-07-22
    dt = _local_dt(2026, 7, 22, 7, 0)
    assert serina_day_id(dt, boundary_hour=7) == date(2026, 7, 22)


def test_is_serina_day_boundary_instant_true_at_exact_boundary() -> None:
    local = datetime(2026, 7, 21, 7, 0, 0, tzinfo=JST)
    assert is_serina_day_boundary_instant(local, boundary_hour=7) is True


def test_is_serina_day_boundary_instant_false_for_legacy_noon_default() -> None:
    """2026-07-26是正(C-1): legacy投入記憶が時刻不明時のデフォルトとして多用する
    12:00:00ちょうど（実測: 28件中13件）を誤って境界揃えと判定しないこと。
    分秒0だけでなくhour==boundary_hourまで絞る必要がある回帰テスト。"""
    local = datetime(2026, 3, 21, 12, 0, 0, tzinfo=JST)
    assert is_serina_day_boundary_instant(local, boundary_hour=7) is False


def test_is_serina_day_boundary_instant_false_when_seconds_nonzero() -> None:
    local = datetime(2026, 7, 21, 7, 0, 3, tzinfo=JST)
    assert is_serina_day_boundary_instant(local, boundary_hour=7) is False


def test_crossed_serina_day_boundary_across_7am() -> None:
    before = _local_dt(2026, 7, 22, 6, 30)
    after = _local_dt(2026, 7, 22, 7, 30)
    assert crossed_serina_day_boundary(before, after, boundary_hour=7) is True


def test_crossed_serina_day_boundary_same_day() -> None:
    a = _local_dt(2026, 7, 22, 10, 0)
    b = _local_dt(2026, 7, 22, 15, 0)
    assert crossed_serina_day_boundary(a, b, boundary_hour=7) is False


def test_should_run_day_boundary_false_when_grace_not_elapsed() -> None:
    now = _local_dt(2026, 7, 22, 8, 0)
    last_activity = now - timedelta(minutes=5)
    assert should_run_day_boundary(
        now=now,
        last_activity_at=last_activity,
        last_boundary_serina_day=date(2026, 7, 21),
        grace_seconds=900,
        boundary_hour=7,
    ) is False


def test_should_run_day_boundary_true_when_new_day_and_grace_elapsed() -> None:
    now = _local_dt(2026, 7, 22, 8, 0)
    last_activity = now - timedelta(minutes=20)
    assert should_run_day_boundary(
        now=now,
        last_activity_at=last_activity,
        last_boundary_serina_day=date(2026, 7, 21),
        grace_seconds=900,
        boundary_hour=7,
    ) is True


def test_should_run_day_boundary_true_when_never_processed() -> None:
    now = _local_dt(2026, 7, 22, 8, 0)
    last_activity = now - timedelta(minutes=20)
    assert should_run_day_boundary(
        now=now,
        last_activity_at=last_activity,
        last_boundary_serina_day=None,
        grace_seconds=900,
        boundary_hour=7,
    ) is True


def test_should_run_day_boundary_false_when_already_processed_today() -> None:
    now = _local_dt(2026, 7, 22, 8, 0)
    last_activity = now - timedelta(minutes=20)
    assert should_run_day_boundary(
        now=now,
        last_activity_at=last_activity,
        last_boundary_serina_day=date(2026, 7, 22),
        grace_seconds=900,
        boundary_hour=7,
    ) is False
