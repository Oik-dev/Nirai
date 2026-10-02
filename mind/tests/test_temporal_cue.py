"""予定登録 配線B: 日時気配の決定論検知。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.context.temporal_cue import (
    SCHEDULE_TEMPORAL_CUE_NOTE,
    extract_schedule_datetime,
    has_schedule_temporal_cue,
)
from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 7, 27, 12, 0, tzinfo=JST)


def test_cue_note_is_factual_not_imperative() -> None:
    assert "補足" in SCHEDULE_TEMPORAL_CUE_NOTE
    assert "提案" not in SCHEDULE_TEMPORAL_CUE_NOTE
    assert "しろ" not in SCHEDULE_TEMPORAL_CUE_NOTE
    assert "せよ" not in SCHEDULE_TEMPORAL_CUE_NOTE


def test_future_cues_fire() -> None:
    assert has_schedule_temporal_cue("明日15時に病院")
    assert has_schedule_temporal_cue("明後日の朝、打ち合わせ")
    assert has_schedule_temporal_cue("3日後に引っ越す")
    assert has_schedule_temporal_cue("2か月後に旅行")
    assert has_schedule_temporal_cue("来週の金曜ね")
    assert has_schedule_temporal_cue("8月3日に帰る")
    assert has_schedule_temporal_cue("今日の15時に電話する")
    assert has_schedule_temporal_cue("14:30に会おう")


def test_past_and_plain_do_not_fire() -> None:
    assert not has_schedule_temporal_cue("昨日の夕飯おいしかった")
    assert not has_schedule_temporal_cue("一昨日は雨だった")
    assert not has_schedule_temporal_cue("先日の話だけど")
    assert not has_schedule_temporal_cue("おはよう、元気？")
    assert not has_schedule_temporal_cue("")
    # 「今日」単独は弱すぎる（時刻・午前午後が無い）
    assert not has_schedule_temporal_cue("今日はいい天気だね")


def test_extract_absolute_month_day() -> None:
    got = extract_schedule_datetime("8月3日に帰る", NOW)
    assert got == datetime(2026, 8, 3, 0, 0, tzinfo=JST)


def test_extract_relative_tomorrow_with_time() -> None:
    got = extract_schedule_datetime("明日15時に病院", NOW)
    assert got == datetime(2026, 7, 28, 15, 0, tzinfo=JST)


def test_extract_relative_after_days() -> None:
    got = extract_schedule_datetime("3日後に引っ越す", NOW)
    assert got == datetime(2026, 7, 30, 0, 0, tzinfo=JST)


def test_extract_clock_colon_with_today() -> None:
    got = extract_schedule_datetime("今日の14:30に電話する", NOW)
    assert got == datetime(2026, 7, 27, 14, 30, tzinfo=JST)


def test_extract_combination_month_day_and_time() -> None:
    # 7/7 は NOW(7/27)より過去 → 翌年へ繰り上げ
    got = extract_schedule_datetime("7月7日の19時から花火", NOW)
    assert got == datetime(2027, 7, 7, 19, 0, tzinfo=JST)


def test_extract_past_month_day_rolls_to_next_year() -> None:
    """今年すでに過ぎた月日は来年になる（C-1）。"""
    now = datetime(2026, 12, 15, 12, 0, tzinfo=JST)
    got = extract_schedule_datetime("1月5日に帰る", now)
    assert got == datetime(2027, 1, 5, 0, 0, tzinfo=JST)


def test_extract_future_month_day_keeps_this_year() -> None:
    got = extract_schedule_datetime("8月3日に帰る", NOW)
    assert got == datetime(2026, 8, 3, 0, 0, tzinfo=JST)


def test_extract_fails_when_no_cue() -> None:
    assert extract_schedule_datetime("おはよう、元気？", NOW) is None
    assert extract_schedule_datetime("", NOW) is None


def test_extract_fails_when_ambiguous_two_dates() -> None:
    assert extract_schedule_datetime("明日と来週のどちらでも", NOW) is None
    assert extract_schedule_datetime("7月7日と8月3日", NOW) is None


def test_extract_fails_when_time_only() -> None:
    """日付アンカー無しの時刻のみは fail-closed。"""
    assert extract_schedule_datetime("14:30に会おう", NOW) is None


def test_extract_fails_today_without_time() -> None:
    assert extract_schedule_datetime("今日はいい天気だね", NOW) is None
