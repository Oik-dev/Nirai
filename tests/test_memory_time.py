"""想起記憶の時間ラベル。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.context.memory_time import format_recalled_memory, relative_day_label
from serina.core.context.pack import build_context_pack
from serina.core.memory.store import MemoryRecord
from serina.core.state.session import SessionState

JST = ZoneInfo("Asia/Tokyo")


def _memory(content: str, created_at: str) -> MemoryRecord:
    return MemoryRecord(
        id=1,
        type="fact",
        content=content,
        importance=0.5,
        sensitivity_grade=0,
        protection_grade="B",
        cosmetic_version=None,
        created_at=created_at,
        last_accessed=created_at,
    )


def test_relative_day_label_buckets() -> None:
    today = datetime(2026, 7, 21, tzinfo=JST).date()
    assert relative_day_label(today, today=today) == "今日"
    assert relative_day_label(today.replace(day=20), today=today) == "昨日"
    assert relative_day_label(today.replace(day=11), today=today) == "10日前"
    assert relative_day_label(datetime(2025, 3, 21, tzinfo=JST).date(), today=today) == "2025-03-21"


def test_format_recalled_memory_today_and_old() -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=JST)
    today_mem = _memory("今日の約束", "2026-07-21T03:00:00+00:00")  # JST 12:00 同日
    old_mem = _memory("昔の約束", "2025-03-21T20:55:00+09:00")
    assert format_recalled_memory(today_mem, now=now).startswith("[2026-07-21・今日]")
    assert format_recalled_memory(old_mem, now=now).startswith("[2025-03-21]")
    assert "昔の約束" in format_recalled_memory(old_mem, now=now)


def test_pack_injects_time_label_on_recalled_memories() -> None:
    now = datetime(2026, 7, 21, 12, 0, tzinfo=JST)
    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="ルール",
        session=SessionState(),
        master_utterance="調子はどう？",
        recalled_memories=[_memory("信じてくれる？と尋ねた", "2025-03-21T20:55:00+09:00")],
        now=now,
    )
    assert any(m.startswith("[2025-03-21]") and "信じてくれる" in m for m in pack.long_term_memories)
