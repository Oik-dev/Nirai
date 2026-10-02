"""記憶DBアクセス層のテスト。設計書 §4.2, §4.4（2026-07-17改訂: 足し算の活性化モデル）

活性化値 = 基礎活性(重要度+鮮度+等級A/Sの下駄) + 話題近接 + 連想伝播(1ホップ) + ゆらぎ。
ここではnoise_sigma=0（決定論）またはseed済み乱数で各部品の性質を固定する。
ゆらぎ込みの実測ヒット率は tests/eval_recall.py（手動・実DB）が担う。
"""

from __future__ import annotations

import random
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore, RecallParams


def _fake_embedder() -> OllamaEmbedder:
    """contentの先頭文字によって決め打ちベクトルを返す（テスト用・決定論的）

    "浜"は海(0.8)にも崖(0.81)にも近い中継ノード、"崖"は海から遠い(0.3)、
    "凪"は海との類似0.5（足切りと下駄の境界を作るための中間距離）。
    """
    vectors = {
        "天": [1.0, 0.0, 0.0, 0.0],
        "海": [0.0, 1.0, 0.0, 0.0],
        "山": [0.0, 0.0, 1.0, 0.0],
        "浜": [0.0, 0.8, 0.6, 0.0],
        "崖": [0.0, 0.3, 0.954, 0.0],
        "凪": [0.0, 0.5, 0.866, 0.0],
    }

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [0.0, 0.0, 0.0, 1.0])

    return OllamaEmbedder(call_fn=call_fn)


# 決定論の基準パラメータ: ゆらぎなし・伝播なし（各部品を単独で観測するための土台）
FLAT = RecallParams(noise_sigma=0.0, spread_decay=0.0)


def _fresh_store(params: RecallParams = FLAT, rng: random.Random | None = None) -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4, recall_params=params, rng=rng)


def test_add_and_recall_returns_closest_by_relevance() -> None:
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.5)
    store.add_memory("海に行った思い出", type="event", importance=0.5)
    store.add_memory("山登りの計画", type="event", importance=0.5)

    results = store.recall("天気の話題", top_k=1)

    assert len(results) == 1
    assert results[0].content == "天気がいい日の話"


def test_recall_ranks_by_importance_when_relevance_tied() -> None:
    """§4.4: 話題近接が同じなら基礎活性（重要度）の差で順位が決まる"""
    store = _fresh_store()
    store.add_memory("天気の話その1", type="fact", importance=0.1)
    store.add_memory("天気の話その2", type="fact", importance=0.9)

    results = store.recall("天気の話題", top_k=2)

    assert results[0].content == "天気の話その2", "重要度が高い記憶が上位に来るべき"


def test_recall_floor_drops_unrelated_memory_even_if_important() -> None:
    """§4.4付帯ルール2（足切り）: 話題と無関係な記憶は、重要度が最大でも
    活性値が閾値に届かず浮上しない（重要記憶が空気を読まず毎回登場する副作用の抑制）"""
    store = _fresh_store()
    store.add_memory("海の日の記録", type="fact", importance=1.0)  # queryと無関係なベクトル
    store.add_memory("天気の話", type="fact", importance=0.01)  # queryとほぼ同一ベクトル

    results = store.recall("天気の話題", top_k=2)

    contents = [r.content for r in results]
    assert "天気の話" in contents
    assert "海の日の記録" not in contents, "無関係な記憶は足切りで沈黙すべき（件数枠が余っていても）"


def test_recall_grade_bonus_surfaces_promise_near_topic() -> None:
    """§4.4付帯ルール1（下駄）: 話題との近さが中間（足切り境界の下）でも、
    保護等級Sなら下駄で浮上する。同条件の等級Bは沈黙したまま"""
    store = _fresh_store()
    store.add_memory("凪いだ海でかわした約束", type="promise", importance=0.5, protection_grade="S")
    store.add_memory("凪いだ日のただのメモ", type="fact", importance=0.5, protection_grade="B")

    results = store.recall("海の話、覚えてる？", top_k=5)

    contents = [r.content for r in results]
    assert "凪いだ海でかわした約束" in contents, "等級Sは下駄で浮上すべき"
    assert "凪いだ日のただのメモ" not in contents, "同条件の等級Bは足切りされるべき"


def test_recall_spreads_activation_one_hop() -> None:
    """§4.4連想伝播: 発話と直接は遠い記憶が、一次発火した記憶（浜）との
    意味的な近さを経由して1ホップで浮上する（芋づる式）。伝播を切ると沈黙する"""
    with_spread = RecallParams(noise_sigma=0.0, spread_decay=0.5)
    store = _fresh_store(params=with_spread)
    store.add_memory("浜辺を歩いた思い出", type="event", importance=0.5)  # 海に近い一次発火
    store.add_memory("崖の上で見た夕日", type="event", importance=0.5)  # 海から遠いが浜に近い

    results = store.recall("海の話", top_k=5)
    contents = [r.content for r in results]
    assert "崖の上で見た夕日" in contents, "浜経由の連想伝播で浮上すべき"

    store_flat = _fresh_store(params=FLAT)
    store_flat.add_memory("浜辺を歩いた思い出", type="event", importance=0.5)
    store_flat.add_memory("崖の上で見た夕日", type="event", importance=0.5)
    flat_contents = [r.content for r in store_flat.recall("海の話", top_k=5)]
    assert "崖の上で見た夕日" not in flat_contents, "伝播なしでは直接の話題近接だけでは届かないはず"


def test_recall_spread_uses_max_not_sum_across_seeds() -> None:
    """§4.4連想伝播のハブ膨張防止（2026-07-17実測で発見・修正した回帰の錠前）:
    複数seedが同一記憶へ伝播したとき、活性は合算ではなくmax（最も強い1本の経路）に
    限定されるべき。合算だと似た文面の記憶クラスタが二重・三重の加算で肥大化し、
    話題と無関係でも密結合クラスタというだけで正典を上回ってしまう不具合が実測された
    （実DBで「再会」クラスタのヒット率が9-55%まで崩れた）。

    浜・凪はどちらも「海」に近く一次発火のseedになり、かつ互いにも崖に近い
    （浜→崖 類似度≈0.81、凪→崖 類似度≈0.98）。崖への伝播が合算されると
    活性が0.84超まで膨れるが、max方式なら0.6強に収まる。この境界（0.7）で判定する。
    """
    p = RecallParams(noise_sigma=0.0, spread_decay=0.5, spread_seeds=3)
    store = _fresh_store(params=p)
    store.add_memory("浜辺を歩いた思い出", type="event", importance=0.5)
    store.add_memory("凪いだ海の思い出", type="event", importance=0.5)
    target_id = store.add_memory("崖の上で見た夕日", type="event", importance=0.5)

    results = store.recall("海の話", top_k=10)
    target_result = next(r for r in results if r.id == target_id)

    assert target_result.score < 0.7, (
        f"活性値{target_result.score:.4f}が合算時の理論値(~0.84)に近い。"
        "複数seedからの伝播が合算されている（ハブ膨張の再発。max方式に戻すこと）"
    )


def test_recall_noise_is_reproducible_with_seeded_rng() -> None:
    """§4.4ゆらぎ: 乱数源を注入すれば再現可能（本番は毎回違う顔ぶれになりうる）"""
    noisy = RecallParams(noise_sigma=0.1, spread_decay=0.0)
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"

    def _run(seed: int) -> list[int]:
        store = MemoryStore(
            str(db_path), embedder=_fake_embedder(), vector_dim=4,
            recall_params=noisy, rng=random.Random(seed),
        )
        return [r.id for r in store.recall("天気の話題", top_k=3)]

    store = MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)
    store.add_memory("天気の話その1", type="fact", importance=0.5)
    store.add_memory("天気の話その2", type="fact", importance=0.5)
    store.add_memory("天気の話その3", type="fact", importance=0.5)

    assert _run(seed=42) == _run(seed=42), "同じseedなら同じ想起結果になるべき"


def test_recall_refreshes_last_accessed_and_access_count() -> None:
    """§4.1: 想起されるたび鮮度回復。使われなければ緩やかに埋没（Bのみ・§4.7）"""
    import sqlite3

    store = _fresh_store()
    memory_id = store.add_memory("天気の話", type="fact", importance=0.5)

    # 想起前の状態を古いタイムスタンプに書き換える
    conn = sqlite3.connect(store._db_path)  # noqa: SLF001
    conn.execute(
        "UPDATE memories SET last_accessed = '2020-01-01T00:00:00+00:00', access_count = 0 WHERE id = ?",
        (memory_id,),
    )
    conn.commit()
    conn.close()

    store.recall("天気の話題", top_k=1)

    conn = sqlite3.connect(store._db_path)  # noqa: SLF001
    row = conn.execute("SELECT last_accessed, access_count FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()

    assert row[0] != "2020-01-01T00:00:00+00:00", "想起されたのにlast_accessedが更新されていない"
    assert row[1] == 1, "想起されたのにaccess_countが加算されていない"


def test_nearest_relevance_does_not_refresh_last_accessed() -> None:
    """重複チェック(nearest_relevance)はセリナへの想起ではないため鮮度回復させない"""
    import sqlite3

    store = _fresh_store()
    memory_id = store.add_memory("天気の話", type="fact", importance=0.5)
    conn = sqlite3.connect(store._db_path)  # noqa: SLF001
    conn.execute(
        "UPDATE memories SET last_accessed = '2020-01-01T00:00:00+00:00' WHERE id = ?", (memory_id,),
    )
    conn.commit()
    conn.close()

    store.nearest_relevance("天気の話題")

    conn = sqlite3.connect(store._db_path)  # noqa: SLF001
    row = conn.execute("SELECT last_accessed FROM memories WHERE id = ?", (memory_id,)).fetchone()
    conn.close()
    assert row[0] == "2020-01-01T00:00:00+00:00"


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


def test_connect_enables_wal_and_busy_timeout() -> None:
    """session_store と同定石: 本体DBも WAL + busy_timeout（合意台帳 OSS #8）。"""
    store = _fresh_store()
    conn = store._connect()
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert str(mode).lower() == "wal"
        assert int(timeout) >= 5000
    finally:
        conn.close()


def test_write_and_read_ports_delegate() -> None:
    """B3: store.write / store.read 二口が既存メソッドへ委譲する。"""
    store = _fresh_store()
    mid = store.write.add_memory("海に行った思い出", type="event", importance=0.5)
    assert isinstance(mid, int)
    results = store.read.recall("海の話題", top_k=1)
    assert len(results) == 1
    assert results[0].content == "海に行った思い出"


def test_rebuild_vector_index_restores_recall() -> None:
    """B6: rebuild 後も想起できる。"""
    store = _fresh_store()
    store.add_memory("海に行った思い出", type="event", importance=0.5)
    store.add_memory("山登りの計画", type="event", importance=0.5)
    n = store.rebuild_vector_index()
    assert n == 2
    results = store.recall("海の話題", top_k=1)
    assert results[0].content == "海に行った思い出"


def test_distillation_key_roundtrip() -> None:
    store = _fresh_store()
    assert store.has_distillation_key("abc") is False
    store.record_distillation_key("abc")
    assert store.has_distillation_key("abc") is True
    store.record_distillation_key("abc")  # 二重記録しても落ちない
    assert store.has_distillation_key("abc") is True


def main() -> None:
    tests = [
        test_add_and_recall_returns_closest_by_relevance,
        test_recall_ranks_by_importance_when_relevance_tied,
        test_recall_floor_drops_unrelated_memory_even_if_important,
        test_recall_grade_bonus_surfaces_promise_near_topic,
        test_recall_spreads_activation_one_hop,
        test_recall_spread_uses_max_not_sum_across_seeds,
        test_recall_noise_is_reproducible_with_seeded_rng,
        test_recall_refreshes_last_accessed_and_access_count,
        test_nearest_relevance_does_not_refresh_last_accessed,
        test_nearest_relevance_returns_pure_similarity_score,
        test_nearest_relevance_returns_none_for_empty_store,
        test_connect_enables_wal_and_busy_timeout,
        test_write_and_read_ports_delegate,
        test_rebuild_vector_index_restores_recall,
        test_distillation_key_roundtrip,
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
