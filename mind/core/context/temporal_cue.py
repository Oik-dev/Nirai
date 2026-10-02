"""予定登録の日時表現検知（決定論）。

2026-07-26: パックへの補助灯（配線B）は実測で差が出ず不採用（配線A）。
本モジュールは関所側（書いてよいか）での再利用を想定して残す。
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, timedelta, time

# 関所・実験用。パック注入には使わない（配線A）。
SCHEDULE_TEMPORAL_CUE_NOTE = "（補足: この発言に日時の表現がある）"

# 固定語（未来・予定寄り）。過去想起語は入れない。
_FIXED_FUTURE_CUES = (
    "明日",
    "明後日",
    "明々後日",
    "来週",
    "再来週",
    "来月",
    "再来月",
    "今週",
)

_RELATIVE_AFTER = re.compile(
    r"(\d+|[一二三四五六七八九十百]+)\s*(日|週間|週|か?月|ヶ月|カ月|ケ月)\s*後"
)
_MONTH_DAY = re.compile(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_CLOCK_HM = re.compile(r"(\d{1,2})\s*時(\s*(\d{1,2})\s*分)?")
_CLOCK_COLON = re.compile(r"(\d{1,2}):(\d{2})")
_TODAY_WITH_TIME = re.compile(r"今日.{0,12}(\d{1,2}\s*時|\d{1,2}:\d{2}|午前|午後)")

_KANJI_DIGITS = {
    "零": 0, "〇": 0,
    "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def has_schedule_temporal_cue(text: str) -> bool:
    """マスター発言に、予定の気づき補助に足る日時表現があるか。

    True でも予定とは限らない（関所が落とす）。False なら補助灯を出さない。
    """
    if not text or not text.strip():
        return False
    for cue in _FIXED_FUTURE_CUES:
        if cue in text:
            return True
    if _RELATIVE_AFTER.search(text):
        return True
    if _MONTH_DAY.search(text):
        return True
    if _CLOCK_HM.search(text) or _CLOCK_COLON.search(text):
        return True
    if _TODAY_WITH_TIME.search(text):
        return True
    return False


def _parse_intish(raw: str) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    if raw.isdigit():
        return int(raw)
    if raw == "十":
        return 10
    if raw == "百":
        return 100
    if "十" in raw:
        left, _, right = raw.partition("十")
        tens = 1 if left == "" else _KANJI_DIGITS.get(left)
        ones = 0 if right == "" else _KANJI_DIGITS.get(right)
        if tens is None or ones is None:
            return None
        return tens * 10 + ones
    return _KANJI_DIGITS.get(raw)


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def _extract_clock(text: str) -> time | None | str:
    """時刻を1つだけ取る。複数なら 'ambiguous'。無しは None。"""
    hits: list[time] = []
    for m in _CLOCK_COLON.finditer(text):
        hits.append(time(int(m.group(1)), int(m.group(2))))
    for m in _CLOCK_HM.finditer(text):
        hour = int(m.group(1))
        minute = int(m.group(3)) if m.group(3) else 0
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            hits.append(time(hour, minute))
    unique = list(dict.fromkeys(hits))
    if len(unique) == 0:
        return None
    if len(unique) > 1:
        return "ambiguous"
    return unique[0]


def _fixed_cue_date(text: str, now: datetime) -> datetime | None | str:
    """固定未来語から日付を1つ取る。複数なら 'ambiguous'。"""
    ordered = (
        ("明々後日", 3),
        ("明後日", 2),
        ("明日", 1),
        ("再来週", 14),
        ("来週", 7),
        ("再来月", None),
        ("来月", None),
        ("今週", 0),
    )
    found: list[datetime] = []
    base = now.astimezone() if now.tzinfo else now
    base_date = base.replace(hour=0, minute=0, second=0, microsecond=0)
    for cue, days in ordered:
        if cue not in text:
            continue
        if cue == "来月":
            found.append(_add_months(base_date, 1))
        elif cue == "再来月":
            found.append(_add_months(base_date, 2))
        elif days is not None:
            found.append(base_date + timedelta(days=days))
    unique = list(dict.fromkeys(found))
    if len(unique) == 0:
        return None
    if len(unique) > 1:
        return "ambiguous"
    return unique[0]


def _relative_after_date(text: str, now: datetime) -> datetime | None | str:
    matches = list(_RELATIVE_AFTER.finditer(text))
    if not matches:
        return None
    if len(matches) > 1:
        return "ambiguous"
    m = matches[0]
    n = _parse_intish(m.group(1))
    if n is None or n <= 0:
        return None
    unit = m.group(2)
    base = now.astimezone() if now.tzinfo else now
    base_date = base.replace(hour=0, minute=0, second=0, microsecond=0)
    if unit == "日":
        return base_date + timedelta(days=n)
    if unit in ("週", "週間"):
        return base_date + timedelta(days=7 * n)
    if "月" in unit:
        return _add_months(base_date, n)
    return None


def _month_day_date(text: str, now: datetime) -> datetime | None | str:
    matches = list(_MONTH_DAY.finditer(text))
    if not matches:
        return None
    if len(matches) > 1:
        return "ambiguous"
    m = matches[0]
    month, day = int(m.group(1)), int(m.group(2))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    base = now.astimezone() if now.tzinfo else now
    try:
        candidate = base.replace(month=month, day=day, hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return None
    # 今年すでに過ぎた月日は翌年へ（例: 12月に「1月5日」→来年1/5）。同日は繰り上げない。
    if candidate.date() < base.date():
        try:
            candidate = candidate.replace(year=base.year + 1)
        except ValueError:
            return None
    return candidate


def extract_schedule_datetime(text: str, now: datetime) -> datetime | None:
    """マスター発言から日時値を決定論的に1つ取り出す。

    該当なし・複数該当で曖昧な場合は None（即時書き込み例外パス不可）。
    相対表現は now 基準。月日のみは now の年を補完し、過ぎていれば翌年へ繰り上げる。
    """
    if not text or not text.strip():
        return None

    base = now.astimezone() if now.tzinfo else now
    date_candidates: list[datetime] = []

    fixed = _fixed_cue_date(text, now)
    if fixed == "ambiguous":
        return None
    if isinstance(fixed, datetime):
        date_candidates.append(fixed)

    relative = _relative_after_date(text, now)
    if relative == "ambiguous":
        return None
    if isinstance(relative, datetime):
        date_candidates.append(relative)

    month_day = _month_day_date(text, now)
    if month_day == "ambiguous":
        return None
    if isinstance(month_day, datetime):
        date_candidates.append(month_day)

    has_today = "今日" in text
    if has_today:
        today = base.replace(hour=0, minute=0, second=0, microsecond=0)
        date_candidates.append(today)

    unique_dates = list(dict.fromkeys(date_candidates))
    if len(unique_dates) > 1:
        return None

    clock = _extract_clock(text)
    if clock == "ambiguous":
        return None

    if not unique_dates:
        return None

    day = unique_dates[0]
    if isinstance(clock, time):
        return day.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)

    # 今日単独（時刻なし）は弱すぎる → None（has_schedule_temporal_cue と同じ方針）
    only_today = (
        has_today
        and not isinstance(fixed, datetime)
        and not isinstance(relative, datetime)
        and not isinstance(month_day, datetime)
    )
    if only_today:
        return None
    return day
