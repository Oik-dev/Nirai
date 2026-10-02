"""日記削除時の材料記憶連鎖削除（§4.8.1）。

アルバムから type=diary を消すとき、§4.5 と同じ材料窓内の「本番蒸留」記憶だけを
物理削除対象として列挙する。削除実行は gui_server 側で confirm_forget 経由。

原典（legacy 投入・source 非空）は日記が参照していても絶対に巻き込まない。
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from serina.core.memory.store import MemoryRecord, MemoryStore

JST = ZoneInfo("Asia/Tokyo")
DIARY_TYPE = "episodic"


def _parse_iso(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _diary_day_start_jst(diary: MemoryRecord) -> datetime:
    """当該日記の Asia/Tokyo 暦日 00:00:00+09:00。"""
    local = _parse_iso(diary.created_at).astimezone(JST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0)


def _material_since_iso(store: MemoryStore, diary: MemoryRecord) -> str:
    """材料窓の since: 直前の日記 created_at、無ければ当該日記の JST 暦日始端。"""
    diary_created = diary.created_at
    prev_candidates = [
        d
        for d in store.list_by_type(DIARY_TYPE, limit=500)
        if d.id != diary.id and d.created_at < diary_created
    ]
    if prev_candidates:
        return max(prev_candidates, key=lambda d: d.created_at).created_at
    # created_at は UTC で保存されているため、JST 暦日始端も UTC に正規化してから
    # 比較用の ISO 文字列にする（+09:00 のまま返すと文字列比較で境界がずれる）。
    return _diary_day_start_jst(diary).astimezone(timezone.utc).isoformat()


def collect_legacy_chunk_children(store: MemoryStore, memory_id: int) -> list[MemoryRecord]:
    """指定記憶を`parent_id`で参照する子記憶を返す（レガシー投入時代の日記チャンク）。

    子は親（日記本文）と同一内容の検索用コピーであり、独立した別内容の記憶ではない。
    §4.8.1: 親を物理削除するとき、レビューを挟まず自動で連鎖削除する対象。
    """
    return store.list_children(memory_id)


def collect_diary_material_targets(store: MemoryStore, diary: MemoryRecord) -> list[MemoryRecord]:
    """日記 D 削除時に連鎖物理削除する材料記憶を返す（§4.8.1）。

    窓: since（直前日記 or D の JST 暦日始端）〜 until=D.created_at（D 自身は除外）。
    対象: type!=diary かつ protection_grade==B かつ非 pinned かつ **非原典（source 空）**。
    原典（legacy の source 付き）は材料窓に入っていても除外する。
    """
    if diary.type != DIARY_TYPE:
        return []

    since_iso = _material_since_iso(store, diary)
    until_iso = diary.created_at

    candidates = store.list_memories_since(since_iso=since_iso, exclude_type=DIARY_TYPE)
    targets: list[MemoryRecord] = []
    for record in candidates:
        if record.created_at > until_iso:
            continue
        if record.id == diary.id:
            continue
        if record.protection_grade != "B":
            continue
        if record.is_inherited:
            continue
        if store.is_pinned(record.id):
            continue
        targets.append(record)
    return targets
