"""想起記憶の時間ラベル（設計: 長期記憶を“いま起きたこと”と誤読させない）。

created_at から相対表現を作り、パック注入時に本文の前へ付ける。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from serina.core.memory.store import MemoryRecord

JST = ZoneInfo("Asia/Tokyo")
FALLBACK_EVENT_DATE = date(2025, 12, 1)

# serina.core.chores.diary.EPISODIC_MEMORY_TYPE と同じ値（context層からchores層への
# 上向き依存を避けるため複製。import連鎖上の実害は無いが層の向きを揃える判断）。
EPISODIC_MEMORY_TYPE = "episodic"
DIARY_TAG = "セリナの記憶"


def parse_memory_instant(created_at: str) -> datetime | None:
    """ISO 時刻文字列を timezone-aware datetime にする。失敗時は None。"""
    raw = (created_at or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def event_date_jst(created_at: str) -> date | None:
    dt = parse_memory_instant(created_at)
    if dt is None:
        return None
    return dt.astimezone(JST).date()


def relative_day_label(event: date, *, today: date) -> str:
    """当日=今日 / 1日前=昨日 / 2〜30日前=N日前 / それ以前=YYYY-MM-DD。"""
    delta = (today - event).days
    if delta < 0:
        # 未来日付は絶対日だけ出す（時計ずれ対策）
        return event.isoformat()
    if delta == 0:
        return "今日"
    if delta == 1:
        return "昨日"
    if delta <= 30:
        return f"{delta}日前"
    return event.isoformat()


def format_recalled_memory(record: MemoryRecord, *, now: datetime | None = None) -> str:
    """想起1件をパック用文字列にする。例: `[昨日] 本文` / `[2025-03-21・セリナの記憶] 本文`。

    episodic記憶(type="episodic")は一人称語りでマスターの発言引用も混じるため、話者取り違え
    防止にタグを付ける（マスター相談 2026-07-23: 日記想起時の話者混同対策）。
    """
    current = now or datetime.now(tz=JST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=JST)
    today = current.astimezone(JST).date()
    event = event_date_jst(record.created_at)
    if event is None:
        event = FALLBACK_EVENT_DATE
    label = relative_day_label(event, today=today)
    # 30日以内は相対語、それ以前は日付そのもの（relative_day_label 済み）
    if label in ("今日", "昨日") or label.endswith("日前"):
        stamp = f"{event.isoformat()}・{label}"
    else:
        stamp = label
    if record.type == EPISODIC_MEMORY_TYPE:
        stamp = f"{stamp}・{DIARY_TAG}"
    return f"[{stamp}] {record.content}"
