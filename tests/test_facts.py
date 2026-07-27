"""Fact 台帳・ULID のテスト。Wave 2 / 合意台帳 §3.3。"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.facts import FactError, FactStore
from serina.core.memory.store import MemoryStore, RecallParams
from serina.core.memory.ulid import new_ulid


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_facts.db"
    return MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def test_new_ulid_is_26_crockford_chars() -> None:
    ulid = new_ulid(now_ms=1_700_000_000_000)
    assert len(ulid) == 26
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", ulid)


def test_new_ulid_is_time_sortable() -> None:
    earlier = new_ulid(now_ms=1_000)
    later = new_ulid(now_ms=2_000)
    assert earlier < later


def test_add_and_get_fact() -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="likes",
        object="コーヒー",
        statement="マスターはコーヒーが好き",
        episode_ids=[1, 2],
        status="active",
    )
    fact = store.facts.get_fact(fid)
    assert fact is not None
    assert fact.statement == "マスターはコーヒーが好き"
    assert fact.episode_ids == [1, 2]
    assert fact.status == "active"


def test_list_active_facts_excludes_superseded() -> None:
    store = _fresh_store()
    old_id = store.facts.add_fact(
        subject="a",
        predicate="b",
        object="c",
        statement="旧",
        episode_ids=[1],
        status="active",
    )
    store.facts.supersede_fact(
        old_id,
        subject="a",
        predicate="b",
        object="c",
        statement="新",
        episode_ids=[2],
    )
    active = store.facts.list_active_facts()
    assert len(active) == 1
    assert active[0].statement == "新"
    old = store.facts.get_fact(old_id)
    assert old is not None
    assert old.status == "superseded"
    assert old.valid_to is not None


def test_hypothesis_promotion_rejects_empty_episodes() -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="x",
        predicate="y",
        object="z",
        statement="仮説",
        status="hypothesis",
    )
    try:
        store.facts.promote_hypothesis_to_active(fid, episode_ids=[])
        raise AssertionError("空 episode_ids で昇格が通ってしまった")
    except FactError:
        pass


def test_hypothesis_promotion_succeeds_with_episodes() -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="x",
        predicate="y",
        object="z",
        statement="仮説",
        status="hypothesis",
    )
    store.facts.promote_hypothesis_to_active(fid, episode_ids=[10])
    fact = store.facts.get_fact(fid)
    assert fact is not None
    assert fact.status == "active"
    assert fact.episode_ids == [10]


def test_add_active_fact_rejects_empty_episodes() -> None:
    store = _fresh_store()
    try:
        store.facts.add_fact(
            subject="a",
            predicate="b",
            object="c",
            statement="即 active",
            status="active",
            episode_ids=[],
        )
        raise AssertionError("active + 空 episode_ids が通ってしまった")
    except FactError:
        pass


def test_write_read_api_delegates_facts() -> None:
    store = _fresh_store()
    fid = store.write.add_fact(
        subject="a",
        predicate="b",
        object="c",
        statement="委譲",
        episode_ids=[1],
        status="active",
    )
    assert store.read.get_fact(fid) is not None
    assert len(store.read.list_active_facts()) == 1


def test_fact_store_standalone() -> None:
    db_path = Path(tempfile.mkdtemp()) / "facts_only.db"
    fs = FactStore(str(db_path))
    fid = fs.add_fact(
        subject="s",
        predicate="p",
        object="o",
        statement="単体",
        episode_ids=[1],
    )
    assert fs.get_fact(fid) is not None


# --- 2026-07-26 B2: fact埋め込み（facts_vec）の保存・読み出し ------------------


def test_add_fact_with_embedding_is_retrievable() -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1], embedding=[0.1, 0.2, 0.3, 0.4],
    )
    got = store.facts.get_fact_embedding(fid)
    assert got is not None
    for a, b in zip(got, [0.1, 0.2, 0.3, 0.4]):
        assert abs(a - b) < 1e-5


def test_get_fact_embedding_none_when_not_saved() -> None:
    """embedding未指定でadd_factしたfactはfacts_vecに無い（None）。"""
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1],
    )
    assert store.facts.get_fact_embedding(fid) is None


def test_save_fact_embedding_backfills_existing_fact() -> None:
    """遅延移行: 後から埋め込みを保存できる（移行前データ向け）。"""
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1],
    )
    assert store.facts.get_fact_embedding(fid) is None
    store.facts.save_fact_embedding(fid, [0.5, 0.5, 0.0, 0.0])
    got = store.facts.get_fact_embedding(fid)
    assert got is not None
    assert abs(got[0] - 0.5) < 1e-5


def test_rebuild_facts_vector_index_rewrites_all() -> None:
    """埋め込みモデル差し替え想定: facts_vecを全文から作り直せる。"""
    store = _fresh_store()
    fid1 = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1], embedding=[0.1, 0.0, 0.0, 0.0],
    )
    fid2 = store.facts.add_fact(
        subject="マスター", predicate="likes", object="紅茶",
        statement="紅茶が好き", episode_ids=[2],
    )
    assert store.facts.get_fact_embedding(fid2) is None
    n = store.rebuild_facts_vector_index()
    assert n == 2
    # fake embedderは常に[1,0,0,0]を返す → 既存も上書きされる
    got1 = store.facts.get_fact_embedding(fid1)
    got2 = store.facts.get_fact_embedding(fid2)
    assert got1 is not None and abs(got1[0] - 1.0) < 1e-5
    assert got2 is not None and abs(got2[0] - 1.0) < 1e-5


def test_supersede_fact_saves_new_facts_embedding() -> None:
    store = _fresh_store()
    old_id = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1], embedding=[1.0, 0.0, 0.0, 0.0],
    )
    new_id = store.facts.supersede_fact(
        old_id,
        subject="マスター", predicate="likes", object="旅行",
        statement="旅行が好き", episode_ids=[2], embedding=[0.0, 1.0, 0.0, 0.0],
    )
    got = store.facts.get_fact_embedding(new_id)
    assert got is not None
    assert abs(got[1] - 1.0) < 1e-5
    # 旧factの埋め込みは残ったまま（消していない）
    assert store.facts.get_fact_embedding(old_id) is not None


# --- Task 0-2: tombstone_fact のembedding削除拡張 ----------------------------


def test_tombstone_fact_deletes_embedding() -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1], embedding=[1.0, 0.0, 0.0, 0.0],
    )
    assert store.facts.get_fact_embedding(fid) is not None
    store.facts.tombstone_fact(fid)
    assert store.facts.get_fact_embedding(fid) is None
    fact = store.facts.get_fact(fid)
    assert fact is not None
    assert fact.status == "tombstone"


def test_rebuild_embeddings_excludes_tombstone_rows() -> None:
    store = _fresh_store()
    fid_active = store.facts.add_fact(
        subject="マスター", predicate="likes", object="散歩",
        statement="散歩が好き", episode_ids=[1], embedding=[1.0, 0.0, 0.0, 0.0],
    )
    fid_tombstone = store.facts.add_fact(
        subject="マスター", predicate="likes", object="旅行",
        statement="旅行が好き", episode_ids=[2], embedding=[0.0, 1.0, 0.0, 0.0],
    )
    store.facts.tombstone_fact(fid_tombstone)
    assert store.facts.get_fact_embedding(fid_tombstone) is None

    n = store.rebuild_facts_vector_index()

    assert n == 1  # tombstone行は再構築対象に含めない
    got_active = store.facts.get_fact_embedding(fid_active)
    assert got_active is not None and abs(got_active[0] - 1.0) < 1e-5
    # tombstone行はrebuild後もembeddingを持たないまま
    assert store.facts.get_fact_embedding(fid_tombstone) is None
