"""explainable recall のテスト。Wave 3 B5。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.store import MemoryStore, RecallParams


def _fake_embedder() -> OllamaEmbedder:
    vectors = {
        "天": [1.0, 0.0, 0.0, 0.0],
        "海": [0.0, 1.0, 0.0, 0.0],
    }

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [0.0, 0.0, 0.0, 1.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def test_recall_explain_attaches_score_breakdown_keys() -> None:
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.5)

    results = store.recall("天気の話題", top_k=1, explain=True)

    assert len(results) == 1
    explanation = results[0].explanation
    assert explanation is not None
    for key in ("relevance", "importance", "recency", "grade_bonus", "spread", "noise", "activation"):
        assert hasattr(explanation, key), f"内訳キー {key} が欠落"


def test_recall_default_explain_false_keeps_compat() -> None:
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.5)

    results = store.recall("天気の話題", top_k=1)

    assert results[0].explanation is None
