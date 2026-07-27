"""予定登録の日時表現検知（決定論）。

2026-07-26: パックへの補助灯（配線B）は実測で差が出ず不採用（配線A）。
本モジュールは関所側（書いてよいか）での再利用を想定して残す。
"""

from __future__ import annotations

import re

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
