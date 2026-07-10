"""記憶DBアクセス層のテスト。設計書v2 §4.2, §4.4

新規実装（旧memory/store.py, memory/db.pyは参照しない）。
関連度×新しさ×重要度のかけ算で上位想起 ＋ 保護等級A/Sのキーワードトリガー想起。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.memory.embedder import OllamaEmbedder
from serina.core_v2.memory.store import MemoryStore


def _fake_embedder() -> OllamaEmbedder:
    """contentの先頭文字によって決め打ちベクトルを返す（テスト用・決定論的）"""
    vectors = {
        "天": [1.0, 0.0, 0.0, 0.0],
        "海": [0.0, 1.0, 0.0, 0.0],
        "山": [0.0, 0.0, 1.0, 0.0],
    }

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [0.0, 0.0, 0.0, 1.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def test_add_and_recall_returns_closest_by_relevance() -> None:
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.5)
    store.add_memory("海に行った思い出", type="event", importance=0.5)
    store.add_memory("山登りの計画", type="event", importance=0.5)

    results = store.recall("天気の話題", top_k=1)

    assert len(results) == 1
    assert results[0].content == "天気がいい日の話"


def test_recall_ranks_by_importance_when_relevance_tied() -> None:
    """§4.4: 関連度×新しさ×重要度のかけ算。関連度が同程度なら重要度が高い記憶が上位に来る"""
    store = _fresh_store()
    store.add_memory("天気の話その1", type="fact", importance=0.1)
    store.add_memory("天気の話その2", type="fact", importance=0.9)

    results = store.recall("天気の話題", top_k=2)

    assert results[0].content == "天気の話その2", "重要度が高い記憶が上位に来るべき"


def test_recall_score_is_product_not_sum() -> None:
    """§4.4: 「かけ算」であり加重和ではない。関連度ゼロならどれだけ重要度が高くても0点になる(ANDゲート)"""
    store = _fresh_store()
    store.add_memory("海の日の記録", type="fact", importance=1.0)  # queryと無関係なベクトル
    store.add_memory("天気の話", type="fact", importance=0.01)  # queryとほぼ同一ベクトル

    results = store.recall("天気の話題", top_k=2)

    assert results[0].content == "天気の話", "無関係でも重要度が高いだけで上位に来てはいけない（積の性質）"
    海の日 = next(r for r in results if r.content == "海の日の記録")
    assert 海の日.score == 0.0, "関連度0は加算では消えないが、積では厳密に0になるはず"


def test_recall_by_keyword_finds_promise_regardless_of_relevance() -> None:
    """§4.4: 約束・正典級(保護等級A/S)はキーワードのトリガー想起で確実に拾う"""
    store = _fresh_store()
    store.add_memory("来週の水曜に映画に行く約束", type="promise", protection_grade="A")
    store.add_memory("山登りの計画", type="event", protection_grade="B")

    results = store.recall_by_keyword("映画")

    assert len(results) == 1
    assert results[0].content == "来週の水曜に映画に行く約束"


def test_nearest_relevance_returns_pure_similarity_score() -> None:
    """重複チェック用: 新しさ・重要度を混ぜない純粋な関連度(1-cosine距離)を返す"""
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.9)
    store.add_memory("海に行った思い出", type="event", importance=0.1)

    result = store.nearest_relevance("天気の話題")

    assert result is not None
    record, relevance = result
    assert record.content == "天気がいい日の話"
    assert relevance > 0.9  # ほぼ同一ベクトルなのでcos類似度は1に近い


def test_nearest_relevance_returns_none_for_empty_store() -> None:
    store = _fresh_store()
    assert store.nearest_relevance("天気の話題") is None


def test_recall_by_keyword_ignores_grade_b_memories() -> None:
    store = _fresh_store()
    store.add_memory("映画の感想メモ", type="fact", protection_grade="B")

    results = store.recall_by_keyword("映画")

    assert results == [], "保護等級Bはキーワードトリガー想起の対象外であるべき"


def main() -> None:
    tests = [
        test_add_and_recall_returns_closest_by_relevance,
        test_recall_ranks_by_importance_when_relevance_tied,
        test_recall_score_is_product_not_sum,
        test_nearest_relevance_returns_pure_similarity_score,
        test_nearest_relevance_returns_none_for_empty_store,
        test_recall_by_keyword_finds_promise_regardless_of_relevance,
        test_recall_by_keyword_ignores_grade_b_memories,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
