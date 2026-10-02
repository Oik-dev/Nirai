"""想起記憶の時間ラベル（設計: 長期記憶を“いま起きたこと”と誤読させない）。

created_at から相対表現を作り、パック注入時に本文の前へ付ける。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from mind.core.memory.diary_date import resolve_diary_target_date
from mind.core.memory.store import MemoryRecord

JST = ZoneInfo("Asia/Tokyo")
FALLBACK_EVENT_DATE = date(2025, 12, 1)

# mind.core.chores.diary.EPISODIC_MEMORY_TYPE と同じ値（context層からchores層への
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


def event_date_jst(
    created_at: str,
    *,
    is_diary_day_boundary: bool = False,
    metadata: dict | None = None,
) -> date | None:
    """記憶の「出来事日」をJST暦日で返す。

    日記(episodic)は`resolve_diary_target_date`（metadata.target_date優先、無ければ
    日界ヒューリスティック）に任せる（2026-07-26恒久解）。非日記は`created_at`のJST暦日。
    """
    if is_diary_day_boundary:
        tagged = resolve_diary_target_date(created_at=created_at, metadata=metadata)
        if not tagged:
            return None
        try:
            return date.fromisoformat(tagged)
        except ValueError:
            return None
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
    is_diary = record.type == EPISODIC_MEMORY_TYPE
    event = event_date_jst(
        record.created_at,
        is_diary_day_boundary=is_diary,
        metadata=record.metadata,
    )
    if event is None:
        event = FALLBACK_EVENT_DATE
    label = relative_day_label(event, today=today)
    # 30日以内は相対語、それ以前は日付そのもの（relative_day_label 済み）
    if label in ("今日", "昨日") or label.endswith("日前"):
        stamp = f"{event.isoformat()}・{label}"
    else:
        stamp = label
    if is_diary:
        stamp = f"{stamp}・{DIARY_TAG}"
    return f"[{stamp}] {record.content}"
