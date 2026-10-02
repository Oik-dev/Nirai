"""想起でヒットした意味記憶に、同じ日の日記を機械的に添える（recall 本体は触らない）。

1つの会話から生まれた複数の意味記憶(semantic)が、想起時にバラバラに出てくると
「一緒に起きたことだと繋げて思い出せない」というマスター指摘への対策（2026-08-01）。
episodic(日記)は既に「その日の複数の意味記憶を1本の物語にまとめる」役割を担っている
ことが実データで確認できたため、新しいグループタグ・兄弟記憶展開のような重い仕組みは
作らず、意味記憶ヒットに「同じ日の日記があれば、そのまま(全文)添える」という軽量な
リンクだけを追加する。

architecture-reviewer 2026-08-01 条件付きPASS: 日記の添付は決定論的な受け渡し
（全文もしくは機械的な抜粋）に留め、Core側で要約等の生成は行わないこと。
本モジュールは日記本文を一切書き換えず、そのまま渡す（要約はしない）。
"""

from __future__ import annotations

from datetime import datetime

from mind.core.memory.store import MemoryRecord, MemoryStore
from mind.core.state.serina_day import serina_day_id

# 日記索引を作る際に読む episodic 記憶の上限（1日1本想定のため十分な余裕を持つ）。
DIARY_INDEX_LIMIT = 1000

# 2026-08-01是正(serina-code-reviewer指摘I-4): 意味記憶ヒットが多日にまたがると
# 日記本文（長文の一人称ナラティブ）が文脈パックに何本も乗り、想起ブロックが肥大化する。
# 添える日記は直近優先で上限を設ける。
MAX_ATTACHED_DIARIES = 2


def expand_semantic_with_diary(
    store: MemoryStore,
    records: list[MemoryRecord],
    *,
    boundary_hour: int = 7,
) -> list[MemoryRecord]:
    """ヒット一覧に、意味記憶と同じSerina日の日記（あれば）を足した新しい一覧を返す。

    入力は変更しない（新しいlistを返す）。同じ日記が複数の意味記憶ヒットから
    重複して足されないようにする。日記本文はそのまま(全文)渡す。
    """
    if not records:
        return list(records)

    semantic_hits = [rec for rec in records if rec.type == "semantic" and rec.created_at]
    if not semantic_hits:
        return list(records)

    diary_index = _build_diary_index_by_day(store, boundary_hour=boundary_hour)
    if not diary_index:
        return list(records)

    already_present_ids = {rec.id for rec in records}
    added_diary_ids: set[int] = set()
    result = list(records)
    attached_count = 0

    for rec in semantic_hits:
        if attached_count >= MAX_ATTACHED_DIARIES:
            break
        day = _serina_day_key(rec.created_at, boundary_hour=boundary_hour)
        if day is None:
            continue
        diary = diary_index.get(day)
        if diary is None:
            continue
        if diary.id in already_present_ids or diary.id in added_diary_ids:
            continue
        added_diary_ids.add(diary.id)
        result.append(diary)
        attached_count += 1

    return result


def _build_diary_index_by_day(
    store: MemoryStore, *, boundary_hour: int,
) -> dict[str, MemoryRecord]:
    """metadata.target_dateを持つepisodic記憶を、対象日文字列 -> MemoryRecord の索引にする。

    target_dateが無い日記（旧レガシー投入分等）は索引に載らず、単に添えられないだけ
    （エラーにはしない）。

    2026-08-01是正(serina-code-reviewer指摘I-2): `list_by_type`はcreated_at DESC
    （新しい順）で返るため、同一target_dateの日記が複数存在する場合（本来あってはならない
    状態だが、水位巻き戻り事故のような異常時に起こり得る）、無条件代入だと最後に見た
    最古の日記が索引に残ってしまう。`not in index`で「最初に見た＝一番新しい」方を
    優先する。
    """
    index: dict[str, MemoryRecord] = {}
    for diary in store.list_by_type("episodic", limit=DIARY_INDEX_LIMIT):
        target_date = _extract_target_date(diary)
        if target_date and target_date not in index:
            index[target_date] = diary
    return index


def _extract_target_date(diary: MemoryRecord) -> str | None:
    meta = diary.metadata
    if isinstance(meta, dict):
        value = meta.get("target_date")
        if isinstance(value, str) and value:
            return value
    return None


def _serina_day_key(created_at: str, *, boundary_hour: int) -> str | None:
    try:
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return None
    return serina_day_id(dt, boundary_hour=boundary_hour).isoformat()
