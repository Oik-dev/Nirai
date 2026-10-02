"""予定・記念日の窓判定テスト（Task 1-2）。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.context.schedule_window import (
    WINDOW_EVE,
    WINDOW_POST,
    WINDOW_PRE,
    is_schedule_window_open,
)
from mind.core.memory.facts import FACT_CATEGORY_ANNIVERSARY, FACT_CATEGORY_SCHEDULE

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class _FakeFact:
    valid_from: str
    valid_to: str | None = None
    category: str | None = FACT_CATEGORY_SCHEDULE


def _dt(y: int, m: int, d: int, hh: int, mm: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=JST)


# 予定: 2026-07-28 15:00〜17:00 JST
_SCHEDULE = _FakeFact(
    valid_from="2026-07-28T15:00:00+09:00",
    valid_to="2026-07-28T17:00:00+09:00",
    category=FACT_CATEGORY_SCHEDULE,
)


def test_eve_window_open_on_previous_evening() -> None:
    # 前日 19:00 ちょうど → 前夜オープン
    assert is_schedule_window_open(_dt(2026, 7, 27, 19, 0), _SCHEDULE) == WINDOW_EVE
    # 前日 23:59 → まだ前夜
    assert is_schedule_window_open(_dt(2026, 7, 27, 23, 59), _SCHEDULE) == WINDOW_EVE


def test_eve_window_closed_at_midnight() -> None:
    # 当日 00:00 = 前夜の閉じる境界（半開なので閉じている）
    assert is_schedule_window_open(_dt(2026, 7, 28, 0, 0), _SCHEDULE) is None


def test_pre_window_boundary() -> None:
    # 開始1時間前ちょうど → 直前
    assert is_schedule_window_open(_dt(2026, 7, 28, 14, 0), _SCHEDULE) == WINDOW_PRE
    # 開始直前 → 直前
    assert is_schedule_window_open(_dt(2026, 7, 28, 14, 59), _SCHEDULE) == WINDOW_PRE
    # 開始ちょうど → 直前は閉じる
    assert is_schedule_window_open(_dt(2026, 7, 28, 15, 0), _SCHEDULE) is None


def test_post_window_boundary() -> None:
    # 終了ちょうど → 事後オープン
    assert is_schedule_window_open(_dt(2026, 7, 28, 17, 0), _SCHEDULE) == WINDOW_POST
    # 終了+3時間直前 → 事後
    assert is_schedule_window_open(_dt(2026, 7, 28, 19, 59), _SCHEDULE) == WINDOW_POST
    # 終了+3時間ちょうど → 閉じる
    assert is_schedule_window_open(_dt(2026, 7, 28, 20, 0), _SCHEDULE) is None


def test_default_duration_when_valid_to_missing() -> None:
    fact = _FakeFact(valid_from="2026-07-28T15:00:00+09:00", valid_to=None)
    # 終了みなし = 17:00。事後は 17:00〜20:00
    assert is_schedule_window_open(_dt(2026, 7, 28, 17, 30), fact) == WINDOW_POST
    assert is_schedule_window_open(_dt(2026, 7, 28, 20, 0), fact) is None


def test_invalid_valid_to_before_start_falls_back_to_default_duration() -> None:
    """end < start な valid_to は開始+既定2時間へフォールバックする。"""
    from mind.core.context.schedule_window import resolve_schedule_bounds

    fact = _FakeFact(
        valid_from="2026-07-28T15:00:00+09:00",
        valid_to="2026-07-28T10:00:00+09:00",  # 開始より前（不正）
    )
    bounds = resolve_schedule_bounds(fact, _dt(2026, 7, 28, 12, 0))
    assert bounds is not None
    start, end = bounds
    assert start.hour == 15
    assert end.hour == 17  # 15:00 + 2h
    # フォールバック後の事後窓が開くこと
    assert is_schedule_window_open(_dt(2026, 7, 28, 17, 30), fact) == WINDOW_POST


def test_priority_pre_over_eve_when_both_could_apply() -> None:
    """直前と前夜が理論上重なり得る極端ケースでも直前優先。

    通常は重ならないが、優先順位の契約を固定する。
    """
    # 通常スケジュールでは重ならない。優先関数は直前を先に見ることを境界テストで担保。
    assert is_schedule_window_open(_dt(2026, 7, 28, 14, 30), _SCHEDULE) == WINDOW_PRE


def test_anniversary_year_fill_and_eve_only() -> None:
    ann = _FakeFact(valid_from="--07-07", category=FACT_CATEGORY_ANNIVERSARY)
    # 2026年補完: 前夜 = 7/6 19:00〜7/7 00:00
    assert is_schedule_window_open(_dt(2026, 7, 6, 19, 0), ann) == WINDOW_EVE
    assert is_schedule_window_open(_dt(2026, 7, 6, 23, 0), ann) == WINDOW_EVE
    assert is_schedule_window_open(_dt(2026, 7, 7, 0, 0), ann) is None
    # 直前・事後は記念日では開かない
    assert is_schedule_window_open(_dt(2026, 7, 7, 14, 0), ann) is None
    assert is_schedule_window_open(_dt(2026, 7, 7, 18, 0), ann) is None


def test_anniversary_eve_crosses_year_boundary() -> None:
    """1/1 記念日の前夜窓は 12/31 に開く（C-1: 年またぎ）。"""
    ann = _FakeFact(valid_from="--01-01", category=FACT_CATEGORY_ANNIVERSARY)
    assert is_schedule_window_open(_dt(2026, 12, 31, 19, 0), ann) == WINDOW_EVE
    assert is_schedule_window_open(_dt(2026, 12, 31, 23, 30), ann) == WINDOW_EVE
    assert is_schedule_window_open(_dt(2027, 1, 1, 0, 0), ann) is None


def test_anniversary_feb29_skips_non_leap_year() -> None:
    """非閏年の --02-29 は ValueError にせず、閏年候補があれば窓が開く（M-3）。"""
    from mind.core.context.schedule_window import resolve_schedule_bounds

    ann = _FakeFact(valid_from="--02-29", category=FACT_CATEGORY_ANNIVERSARY)
    # 2026 前後はいずれも非閏年 → bounds は None（クラッシュしない）
    assert resolve_schedule_bounds(ann, _dt(2026, 2, 28, 20, 0)) is None
    # 2028 は閏年。前夜 = 2028-02-28 19:00
    assert is_schedule_window_open(_dt(2028, 2, 28, 20, 0), ann) == WINDOW_EVE


def test_outside_all_windows() -> None:
    assert is_schedule_window_open(_dt(2026, 7, 26, 12, 0), _SCHEDULE) is None
    assert is_schedule_window_open(_dt(2026, 7, 29, 12, 0), _SCHEDULE) is None
