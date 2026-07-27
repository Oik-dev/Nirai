"""予定・記念日の窓判定（決定論・副作用なし）。

合意: 前夜 / 直前 / 事後。数値はモジュール定数（運用ツマミとして後から変更可）。
`core/context/temporal_cue.py` と同じく純粋関数・状態を持たない。
"""

from __future__ import annotations

from datetime import datetime, timedelta, time
from typing import Protocol

from serina.core.memory.facts import FACT_CATEGORY_ANNIVERSARY, FACT_CATEGORY_SCHEDULE

# --- 窓パラメータ（断定値。config化の置き場） ---
EVE_OPEN_HOUR = 19  # 前夜: 予定開始日の前日 19:00 に開く
PRE_WINDOW_HOURS = 1  # 直前: 開始 N 時間前に開く
DEFAULT_DURATION_HOURS = 2  # valid_to 未設定時の終了 = 開始 + N 時間
POST_WINDOW_HOURS = 3  # 事後: 終了時刻 + N 時間で閉じる

WINDOW_PRE = "直前"
WINDOW_POST = "事後"
WINDOW_EVE = "前夜"

# 複数窓が同時に開く場合の優先順位（直近性が高いもの優先）
_WINDOW_PRIORITY = (WINDOW_PRE, WINDOW_POST, WINDOW_EVE)


class _ScheduleFactLike(Protocol):
    valid_from: str
    valid_to: str | None
    category: str | None


def _local(now: datetime) -> datetime:
    return now.astimezone() if now.tzinfo is not None else now


def _is_yearless_valid_from(valid_from: str) -> bool:
    """年を持たない月日表現（記念日用）。`--MM-DD` または `--MM-DDTHH:MM[:SS]`。"""
    return valid_from.startswith("--")


def _parse_month_day_fragment(fragment: str) -> tuple[int, int, time]:
    """`--MM-DD` / `--MM-DDTHH:MM[:SS]` から (month, day, clock) を取る。"""
    body = fragment[2:]  # strip leading '--'
    if "T" in body:
        date_part, time_part = body.split("T", 1)
        hour, minute, second = 0, 0, 0
        bits = time_part.split(":")
        if len(bits) >= 1 and bits[0]:
            hour = int(bits[0])
        if len(bits) >= 2 and bits[1]:
            minute = int(bits[1])
        if len(bits) >= 3 and bits[2]:
            second = int(float(bits[2]))
        clock = time(hour, minute, second)
    else:
        date_part = body
        clock = time(0, 0, 0)
    month_s, day_s = date_part.split("-", 1)
    return int(month_s), int(day_s), clock


def resolve_schedule_bounds(fact: _ScheduleFactLike, now: datetime) -> tuple[datetime, datetime] | None:
    """fact の開始・終了時刻を now 基準で解決する。パース不能なら None。

    記念日（年無し valid_from）は now の年を補完する。
    終了未設定は開始 + DEFAULT_DURATION_HOURS。
    """
    local_now = _local(now)
    tz = local_now.tzinfo
    raw_from = (fact.valid_from or "").strip()
    if not raw_from:
        return None

    try:
        if _is_yearless_valid_from(raw_from):
            month, day, clock = _parse_month_day_fragment(raw_from)
            start = datetime(
                local_now.year, month, day, clock.hour, clock.minute, clock.second, tzinfo=tz,
            )
        else:
            start = datetime.fromisoformat(raw_from)
            if start.tzinfo is None and tz is not None:
                start = start.replace(tzinfo=tz)
            start = start.astimezone(tz) if tz is not None else start
    except (ValueError, TypeError):
        return None

    raw_to = (fact.valid_to or "").strip() if fact.valid_to else ""
    if raw_to and not _is_yearless_valid_from(raw_to):
        try:
            end = datetime.fromisoformat(raw_to)
            if end.tzinfo is None and tz is not None:
                end = end.replace(tzinfo=tz)
            end = end.astimezone(tz) if tz is not None else end
        except (ValueError, TypeError):
            end = start + timedelta(hours=DEFAULT_DURATION_HOURS)
    else:
        end = start + timedelta(hours=DEFAULT_DURATION_HOURS)

    return start, end


def _window_bounds(start: datetime, end: datetime) -> dict[str, tuple[datetime, datetime]]:
    """各窓の [開く, 閉じる) 半開区間。"""
    eve_day = (start.date() - timedelta(days=1))
    eve_open = datetime.combine(eve_day, time(EVE_OPEN_HOUR, 0, 0), tzinfo=start.tzinfo)
    eve_close = datetime.combine(start.date(), time(0, 0, 0), tzinfo=start.tzinfo)
    pre_open = start - timedelta(hours=PRE_WINDOW_HOURS)
    post_close = end + timedelta(hours=POST_WINDOW_HOURS)
    return {
        WINDOW_EVE: (eve_open, eve_close),
        WINDOW_PRE: (pre_open, start),
        WINDOW_POST: (end, post_close),
    }


def is_schedule_window_open(now: datetime, fact: _ScheduleFactLike) -> str | None:
    """開いている窓名を返す。該当なしは None。

    優先順位: 直前 > 事後 > 前夜。記念日は前夜のみ。
    """
    bounds = resolve_schedule_bounds(fact, now)
    if bounds is None:
        return None
    start, end = bounds
    local_now = _local(now)
    windows = _window_bounds(start, end)

    category = fact.category
    if category == FACT_CATEGORY_ANNIVERSARY or _is_yearless_valid_from(fact.valid_from or ""):
        allowed = (WINDOW_EVE,)
    elif category == FACT_CATEGORY_SCHEDULE:
        allowed = _WINDOW_PRIORITY
    else:
        return None

    for name in _WINDOW_PRIORITY:
        if name not in allowed:
            continue
        open_at, close_at = windows[name]
        if open_at <= local_now < close_at:
            return name
    return None


def pick_open_schedule_fact(
    now: datetime,
    facts: list[_ScheduleFactLike],
) -> tuple[_ScheduleFactLike, str] | None:
    """開いている予定/記念日を最大1件返す（優先: 直前 > 事後 > 前夜）。

    Task 1-6 のパック差し込み用。発火済みは見ない（窓の開閉のみ）。
    """
    best: tuple[_ScheduleFactLike, str] | None = None
    best_rank = 99
    rank = {name: i for i, name in enumerate(_WINDOW_PRIORITY)}
    for fact in facts:
        window = is_schedule_window_open(now, fact)
        if window is None:
            continue
        r = rank.get(window, 99)
        if r < best_rank:
            best = (fact, window)
            best_rank = r
    return best
