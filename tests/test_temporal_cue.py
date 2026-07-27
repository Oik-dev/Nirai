"""予定登録 配線B: 日時気配の決定論検知。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.context.temporal_cue import (
    SCHEDULE_TEMPORAL_CUE_NOTE,
    has_schedule_temporal_cue,
)


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
