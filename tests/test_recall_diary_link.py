"""意味記憶→当日日記への軽量リンク（断片化対策）の単体テスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.context.recall_diary_link import expand_semantic_with_diary
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp())
    emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    return MemoryStore(str(tmp / "m.db"), embedder=emb, vector_dim=4)


def test_semantic_hit_gets_same_day_diary_attached() -> None:
    """意味記憶がヒットしたとき、同じSerina日の日記があれば結果に添えられる。"""
    store = _fresh_store()
    store.add_memory(
        "セリナ、今日も一緒に頑張ろうって思い出を振り返ってた",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T22:00:00+00:00",
        metadata_obj={"target_date": "2026-07-31"},
    )
    semantic_id = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    pair = store.get_memory_by_id(semantic_id)
    assert pair is not None
    semantic_rec, _ = pair

    out = expand_semantic_with_diary(store, [semantic_rec])

    assert len(out) == 2
    types = {rec.type for rec in out}
    assert types == {"semantic", "episodic"}
    diary_rec = next(rec for rec in out if rec.type == "episodic")
    assert "振り返ってた" in diary_rec.content


def test_no_matching_diary_leaves_result_unchanged() -> None:
    """同じ日の日記が存在しない場合、何も添えられない（エラーにもならない）。"""
    store = _fresh_store()
    semantic_id = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    pair = store.get_memory_by_id(semantic_id)
    assert pair is not None
    semantic_rec, _ = pair

    out = expand_semantic_with_diary(store, [semantic_rec])

    assert len(out) == 1
    assert out[0].id == semantic_rec.id


def test_multiple_same_day_semantic_hits_attach_diary_once() -> None:
    """同じ日の意味記憶が複数ヒットしても、日記は重複せず1回だけ添えられる。"""
    store = _fresh_store()
    store.add_memory(
        "その日のまとめ日記",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T22:00:00+00:00",
        metadata_obj={"target_date": "2026-07-31"},
    )
    id1 = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    id2 = store.add_memory(
        "マスターがずっと一緒にいると約束してくれた",
        type="semantic",
        importance=0.9,
        protection_grade="B",
        created_at="2026-07-31T07:46:01+00:00",
    )
    pair1 = store.get_memory_by_id(id1)
    pair2 = store.get_memory_by_id(id2)
    assert pair1 is not None and pair2 is not None
    rec1, _ = pair1
    rec2, _ = pair2

    out = expand_semantic_with_diary(store, [rec1, rec2])

    diary_hits = [rec for rec in out if rec.type == "episodic"]
    assert len(out) == 3  # semantic 2件 + episodic 1件（重複なし）
    assert len(diary_hits) == 1


def test_diary_already_in_results_is_not_duplicated() -> None:
    """想起結果に既に同じ日記が含まれている場合、二重に追加しない。"""
    store = _fresh_store()
    diary_id = store.add_memory(
        "その日のまとめ日記",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T22:00:00+00:00",
        metadata_obj={"target_date": "2026-07-31"},
    )
    semantic_id = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    diary_pair = store.get_memory_by_id(diary_id)
    semantic_pair = store.get_memory_by_id(semantic_id)
    assert diary_pair is not None and semantic_pair is not None
    diary_rec, _ = diary_pair
    semantic_rec, _ = semantic_pair

    out = expand_semantic_with_diary(store, [diary_rec, semantic_rec])

    assert len(out) == 2


def test_empty_input_returns_empty() -> None:
    store = _fresh_store()
    assert expand_semantic_with_diary(store, []) == []


def test_duplicate_target_date_prefers_newest_diary() -> None:
    """2026-08-01是正の回帰テスト(serina-code-reviewer指摘I-2)。

    同じtarget_dateを持つ日記が複数存在する場合（本来あってはならない状態だが、
    水位巻き戻り事故のような異常時に起こり得る）、索引には一番新しい方が残る。
    `list_by_type`はcreated_at DESCで返るため、無条件代入だと最古が残ってしまう
    バグを踏んでいた。
    """
    store = _fresh_store()
    store.add_memory(
        "古い方の日記（誤生成前）",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T21:00:00+00:00",
        metadata_obj={"target_date": "2026-07-31"},
    )
    store.add_memory(
        "新しい方の日記（本来正しいもの）",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T22:00:00+00:00",
        metadata_obj={"target_date": "2026-07-31"},
    )
    semantic_id = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    pair = store.get_memory_by_id(semantic_id)
    assert pair is not None
    semantic_rec, _ = pair

    out = expand_semantic_with_diary(store, [semantic_rec])

    diary_hits = [rec for rec in out if rec.type == "episodic"]
    assert len(diary_hits) == 1
    assert "新しい方" in diary_hits[0].content


def test_attached_diaries_are_capped_at_max() -> None:
    """2026-08-01是正の回帰テスト(serina-code-reviewer指摘I-4)。

    意味記憶ヒットが多日にまたがると日記本文が何本も添えられ文脈パックが肥大化する
    ため、添える日記の件数には上限(MAX_ATTACHED_DIARIES)がある。
    """
    from serina.core.context.recall_diary_link import MAX_ATTACHED_DIARIES

    store = _fresh_store()
    days = ["2026-07-29", "2026-07-30", "2026-07-31"]
    semantic_recs = []
    for i, day in enumerate(days):
        store.add_memory(
            f"{day}の日記",
            type="episodic",
            importance=0.8,
            protection_grade="A",
            created_at=f"{day}T22:00:00+00:00",
            metadata_obj={"target_date": day},
        )
        sid = store.add_memory(
            f"{day}にあった出来事",
            type="semantic",
            importance=0.7,
            protection_grade="B",
            created_at=f"{day}T10:00:00+00:00",
        )
        pair = store.get_memory_by_id(sid)
        assert pair is not None
        rec, _ = pair
        semantic_recs.append(rec)

    assert len(days) > MAX_ATTACHED_DIARIES  # 前提: 上限を超える日数を用意している

    out = expand_semantic_with_diary(store, semantic_recs)

    diary_hits = [rec for rec in out if rec.type == "episodic"]
    assert len(diary_hits) == MAX_ATTACHED_DIARIES


def test_diary_without_target_date_metadata_is_ignored() -> None:
    """target_dateメタデータが無い日記（旧レガシー投入分等）は索引に載らず、単に添えられない。"""
    store = _fresh_store()
    store.add_memory(
        "target_dateが無い旧レガシー日記",
        type="episodic",
        importance=0.8,
        protection_grade="A",
        created_at="2026-07-31T22:00:00+00:00",
    )
    semantic_id = store.add_memory(
        "マスターがセリナにネット環境をくれた",
        type="semantic",
        importance=0.7,
        protection_grade="B",
        created_at="2026-07-31T16:04:24+00:00",
    )
    pair = store.get_memory_by_id(semantic_id)
    assert pair is not None
    semantic_rec, _ = pair

    out = expand_semantic_with_diary(store, [semantic_rec])

    assert len(out) == 1
