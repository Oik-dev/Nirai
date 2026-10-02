"""想起記憶の時間ラベル。"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.context.memory_time import format_recalled_memory, relative_day_label
from mind.core.context.pack import build_context_pack
from mind.core.memory.store import MemoryRecord
from mind.core.state.session import SessionState

JST = ZoneInfo("Asia/Tokyo")


def _memory(content: str, created_at: str, *, type: str = "fact") -> MemoryRecord:
    return MemoryRecord(
        id=1,
        type=type,
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


def test_format_recalled_memory_tags_diary_with_owner() -> None:
    # 2026-07-26是正C-1で境界揃え判定をhour==7(boundary_hour)まで絞ったため、
    # 20:00:00ちょうど（分秒0だが時=7ではない）は境界揃えと誤検知されない。
    now = datetime(2026, 7, 21, 12, 0, tzinfo=JST)
    diary_mem = _memory("今日は穏やかな一日だった", "2026-07-20T20:00:00+09:00", type="episodic")
    fact_mem = _memory("散歩が好き", "2026-07-20T20:00:00+09:00", type="semantic")
    diary_out = format_recalled_memory(diary_mem, now=now)
    fact_out = format_recalled_memory(fact_mem, now=now)
    assert diary_out.startswith("[2026-07-20・昨日・セリナの記憶]")
    assert "セリナの記憶" not in fact_out


def test_format_recalled_memory_diary_day_boundary_created_at_labels_target_day() -> None:
    """2026-07-26是正(I-a): 日記のcreated_atは対象Serina日の"終わり"（次Serina日の開始
    瞬間、例07:00:00 JST）を指す（core/chores/diary.py参照）。素直にJST変換すると
    常に翌日表示になってしまう不整合を検査する回帰テスト。"""
    now = datetime(2026, 7, 22, 12, 0, tzinfo=JST)
    # 2026-07-20分の日記のcreated_at = 対象日の終わり = 2026-07-21T07:00:00+09:00
    diary_mem = _memory("その日は穏やかだった", "2026-07-21T07:00:00+09:00", type="episodic")
    fact_mem = _memory("同時刻の事実記憶", "2026-07-21T07:00:00+09:00", type="semantic")

    diary_out = format_recalled_memory(diary_mem, now=now)
    fact_out = format_recalled_memory(fact_mem, now=now)

    assert diary_out.startswith("[2026-07-20・2日前・セリナの記憶]"), (
        "日記は境界揃え(分秒0)の瞬間と判断して1日前＝対象日(D)そのものを表示する"
    )
    assert fact_out.startswith("[2026-07-21・"), (
        "日記以外の記憶は従来通り素直にJST変換した日付を表示する（回帰させない）"
    )


def test_format_recalled_memory_prefers_metadata_target_date() -> None:
    """2026-07-26恒久解: metadata.target_dateがあればヒューリスティックより優先する。"""
    now = datetime(2026, 7, 22, 12, 0, tzinfo=JST)
    # created_atは翌日07:00だが、タグは対象日を明示。ヒューリスティック無しでも正しい日。
    diary_mem = MemoryRecord(
        id=1,
        type="episodic",
        content="タグ付き日記",
        importance=0.5,
        sensitivity_grade=0,
        protection_grade="A",
        cosmetic_version=None,
        created_at="2026-07-21T07:00:00+09:00",
        last_accessed="2026-07-21T07:00:00+09:00",
        metadata={"target_date": "2026-07-20"},
    )
    assert format_recalled_memory(diary_mem, now=now).startswith(
        "[2026-07-20・2日前・セリナの記憶]"
    )


def test_format_recalled_memory_legacy_diary_without_day_boundary_alignment_unchanged() -> None:
    """2026-07-26是正(I-a): 境界揃えでない旧形式の日記（生成時刻をそのまま保存していた
    過去のレコード。分秒が0ちょうどではない）には調整をかけない（誤補正の防止）。"""
    now = datetime(2026, 7, 21, 12, 0, tzinfo=JST)
    legacy_diary_mem = _memory("今日は穏やかな一日だった", "2026-07-20T20:13:47+09:00", type="episodic")
    assert format_recalled_memory(legacy_diary_mem, now=now).startswith("[2026-07-20・昨日・セリナの記憶]")


def test_format_recalled_memory_legacy_diary_noon_default_not_misdetected_as_boundary() -> None:
    """2026-07-26是正(C-1・serina-code-reviewer指摘): legacy投入記憶（旧日記）は
    時刻不明時のデフォルトとして12:00:00ちょうどを大量に使う（実測: 28件中13件）。
    分秒0だけで境界揃えと判定すると、これらが誤って1日前に表示されてしまう
    （等級A・正典の日付が壊れる）ため、hour==7(boundary_hour)まで絞って防ぐ。"""
    now = datetime(2026, 3, 22, 12, 0, tzinfo=JST)
    legacy_noon_mem = _memory("継承記憶の一節", "2026-03-21T12:00:00+09:00", type="episodic")
    assert format_recalled_memory(legacy_noon_mem, now=now).startswith("[2026-03-21・昨日・セリナの記憶]")


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
