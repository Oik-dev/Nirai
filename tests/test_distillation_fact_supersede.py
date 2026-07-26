"""facts台帳のsupersede判定（2026-07-23設計改訂）のテスト。

蒸留候補から書かれるfactが、同一subjectを持つ既存active factとbge-m3埋め込みの
コサイン類似度で閾値（既定0.85）以上なら`supersede_fact()`で置き換わること、
未満なら別事実として追加されること、いずれの場合も日本語の変更レポートが
change_logへ残ること（構造レビューI-2・透明性原則）を検証する。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.distillation import write_fact_from_distillation_candidate
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryStore

# テキストごとに固定ベクトルを返すスタブ埋め込み器（実Ollamaを使わず類似度を制御する）。
_VECTORS = {
    "犬が苦手": [1.0, 0.0, 0.0, 0.0],
    "犬が最近すっかり平気になった": [0.95, 0.05, 0.0, 0.0],  # 上と高類似（同じ話題・結論が変化）
    "猫を飼い始めた": [0.0, 0.0, 1.0, 0.0],  # 無関係な話題（低類似）
}


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return _VECTORS.get(text, [0.0, 1.0, 0.0, 0.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        fact_supersede_similarity_threshold=0.85,
    )


def _candidate(statement: str, *, subject: str = "犬") -> dict:
    return {
        "content": statement,
        "confidence": 0.9,
        "importance": 0.6,
        "fact": {
            "subject": subject,
            "predicate": "好み",
            "object": "",
            "statement": statement,
            "status": "active",
        },
    }


def test_second_candidate_supersedes_high_similarity_existing_fact() -> None:
    store = _fresh_store()
    change_log = ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")
    thresholds = _thresholds()

    old_id = write_fact_from_distillation_candidate(
        store,
        _candidate("犬が苦手"),
        episode_ids=[1],
        thresholds=thresholds,
        change_log=change_log,
    )
    assert old_id is not None

    new_id = write_fact_from_distillation_candidate(
        store,
        _candidate("犬が最近すっかり平気になった"),
        episode_ids=[2],
        thresholds=thresholds,
        change_log=change_log,
    )
    assert new_id is not None
    assert new_id != old_id

    old_fact = store.facts.get_fact(old_id)
    new_fact = store.facts.get_fact(new_id)
    assert old_fact.status == "superseded"
    assert new_fact.status == "active"
    assert new_fact.supersedes == old_id

    reports = change_log.read_all()
    assert any(r.action == "fact追加" for r in reports)
    assert any(r.action == "fact supersede" and r.target_id == new_id for r in reports)


def test_unrelated_subject_topic_is_added_separately_not_superseded() -> None:
    store = _fresh_store()
    change_log = ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")
    thresholds = _thresholds()

    dog_id = write_fact_from_distillation_candidate(
        store,
        _candidate("犬が苦手", subject="犬"),
        episode_ids=[1],
        thresholds=thresholds,
        change_log=change_log,
    )
    cat_id = write_fact_from_distillation_candidate(
        store,
        _candidate("猫を飼い始めた", subject="猫"),  # subjectが違うので類似度判定対象外
        episode_ids=[2],
        thresholds=thresholds,
        change_log=change_log,
    )

    dog_fact = store.facts.get_fact(dog_id)
    cat_fact = store.facts.get_fact(cat_id)
    assert dog_fact.status == "active"  # supersedeされていない
    assert cat_fact.status == "active"
    assert cat_fact.supersedes is None


def test_supersede_reuses_cached_embedding_instead_of_reembedding() -> None:
    """2026-07-26 B2: facts_vecに保存済みの埋め込みは再計算しない。

    fact1書き込み時点では比較対象が無く埋め込み未保存。fact2書き込み時に
    fact1を遅延移行で埋め込み・保存し、fact1をsupersedeしてfact2に埋め込みを保存する。
    fact3書き込み時はfact2（唯一のactive）の埋め込みが既にfacts_vecにあるため、
    fact3自身の埋め込み計算1回だけで済む（fact2の再埋め込みが起きない）。
    """
    calls: list[str] = []

    def call_fn(model: str, text: str) -> list[float]:
        calls.append(text)
        return _VECTORS.get(text, [0.0, 1.0, 0.0, 0.0])

    store = MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"),
        embedder=OllamaEmbedder(call_fn=call_fn),
        vector_dim=4,
    )
    change_log = ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")
    thresholds = _thresholds()

    write_fact_from_distillation_candidate(
        store, _candidate("犬が苦手"), episode_ids=[1],
        thresholds=thresholds, change_log=change_log,
    )
    assert calls == []  # 比較対象が無いので埋め込み計算なし

    calls.clear()
    write_fact_from_distillation_candidate(
        store, _candidate("犬が最近すっかり平気になった"), episode_ids=[2],
        thresholds=thresholds, change_log=change_log,
    )
    # fact1（遅延移行で1回）＋fact2自身（1回）＝2回
    assert len(calls) == 2

    calls.clear()
    write_fact_from_distillation_candidate(
        store, _candidate("犬がまた苦手に戻った"), episode_ids=[3],
        thresholds=thresholds, change_log=change_log,
    )
    # 唯一のactive（fact2）はfacts_vecに保存済みのため再計算せず、fact3自身の1回だけ
    assert len(calls) == 1
    assert calls[0] == "犬がまた苦手に戻った"
