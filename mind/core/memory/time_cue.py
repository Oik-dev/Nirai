"""思い出すときの、時の手がかり（docs/設計書.md §4.4）。

「昨日」「3月17日」「去年の3月ごろ」「この前」「最初に話したとき」のような言葉から、その時期（日付の範囲）を読む。
読めた時期の出来事は、思い出しやすくなる。予定の日時を読む core/context/temporal_cue.py は未来の言葉を読むもので、こちらは過去を読む。
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    sharp: bool = True  # はっきりした時期（日付・昨日・何月）か、ぼんやりした時期（この前・最近）か

    def contains(self, day: date) -> bool:
        return self.first <= day <= self.last


def _month(year: int, month: int) -> Period:
    return Period(date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))


def _year(text: str, today: date) -> int | None:
    if "一昨年" in text or "おととし" in text:
        return today.year - 2
    if "去年" in text or "昨年" in text:
        return today.year - 1
    m = re.search(r"(\d{4})年", text)
    return int(m.group(1)) if m else None


def read_period(text: str, now: datetime, *, earliest: date | None = None) -> Period | None:
    """text の中の過去の時期。読めなければ None。earliest は記録の最初の日（「最初に話したとき」に使う）。"""
    text = unicodedata.normalize("NFKC", text).replace(" ", "")
    today = now.date()
    year = _year(text, today)
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        month, day = int(m.group(1)), int(m.group(2))
        if year is None:
            year = today.year if (month, day) <= (today.month, today.day) else today.year - 1
        try:
            return Period(date(year, month, day), date(year, month, day))
        except ValueError:
            return None
    m = re.search(r"(\d{1,2})月", text)
    if m and 1 <= int(m.group(1)) <= 12:
        month = int(m.group(1))
        if year is None:
            year = today.year if month <= today.month else today.year - 1
        return _month(year, month)
    if year is not None:
        return Period(date(year, 1, 1), date(year, 12, 31))
    if "一昨日" in text or "おととい" in text:
        return Period(today - timedelta(days=2), today - timedelta(days=2))
    if any(word in text for word in ("昨日", "きのう", "昨夜", "ゆうべ")):
        return Period(today - timedelta(days=1), today - timedelta(days=1))
    if "先週" in text:
        monday = today - timedelta(days=today.weekday() + 7)
        return Period(monday, monday + timedelta(days=6))
    if "先月" in text:
        last = today.replace(day=1) - timedelta(days=1)
        return _month(last.year, last.month)
    if earliest is not None and re.search(r"(最初|初めて|はじめて)(に|の|て)?(話|会|出会)", text):
        return Period(earliest, earliest + timedelta(days=1))
    if re.search(r"最近|この頃|近ごろ|近頃", text):
        return Period(today - timedelta(days=14), today, sharp=False)
    if re.search(r"この前|こないだ|この間", text):
        return Period(today - timedelta(days=30), today, sharp=False)
    return None
