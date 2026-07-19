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
