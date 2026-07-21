"""想起ヒットを親文書の近傍パッセージへ広げる（recall 本体は触らない）。

日記チャンク（parent_id あり）がヒットしたとき、親本文から当該チャンク位置の
前後を含むつながった文章に差し替える。親なしカードはそのまま通す。
"""

from __future__ import annotations

from dataclasses import replace

from serina.core.memory.store import MemoryRecord, MemoryStore

# 前後に足す文字数目安（チャンク本体と合わせて「半分以上」感を出す）
DEFAULT_PAD_CHARS = 120


def expand_recall_neighbors(
    store: MemoryStore,
    records: list[MemoryRecord],
    *,
    pad_chars: int = DEFAULT_PAD_CHARS,
) -> list[MemoryRecord]:
    """ヒット一覧を近傍展開した新しい一覧を返す（入力は変更しない）。"""
    if not records:
        return []
    expanded: list[MemoryRecord] = []
    seen_passages: set[str] = set()
    for rec in records:
        if rec.parent_id is None:
            expanded.append(rec)
            continue
        parent_pair = store.get_memory_by_id(rec.parent_id)
        if parent_pair is None:
            expanded.append(rec)
            continue
        parent, _pinned = parent_pair
        passage = _neighborhood_passage(parent.content, rec.content, pad_chars=pad_chars)
        # 同一親から同じパッセージが重複しないようにする
        key = f"{parent.id}:{passage[:80]}"
        if key in seen_passages:
            continue
        seen_passages.add(key)
        expanded.append(
            replace(
                rec,
                content=passage,
                # パック上は親の日付感を優先
                created_at=parent.created_at or rec.created_at,
                type=parent.type if parent.type else rec.type,
            )
        )
    return expanded


def _neighborhood_passage(parent_body: str, chunk: str, *, pad_chars: int) -> str:
    """親本文中のチャンク位置を中心に前後 pad して1つの文章を返す。"""
    if not parent_body:
        return chunk
    idx = parent_body.find(chunk)
    if idx < 0:
        # 完全一致しない場合は先頭一致の緩和
        head = chunk[: min(40, len(chunk))]
        idx = parent_body.find(head) if head else -1
        if idx < 0:
            return chunk
        end_hint = idx + max(len(chunk), len(head))
    else:
        end_hint = idx + len(chunk)

    start = max(0, idx - pad_chars)
    end = min(len(parent_body), end_hint + pad_chars)
    # 文／段落境界に寄せる
    if start > 0:
        nl = parent_body.rfind("\n", 0, start + 1)
        if nl >= 0 and start - nl < 80:
            start = nl + 1
    if end < len(parent_body):
        nl = parent_body.find("\n", end - 1)
        if nl >= 0 and nl - end < 80:
            end = nl
    passage = parent_body[start:end].strip()
    return passage or chunk
