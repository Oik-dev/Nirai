"""日記近傍パッセージ展開の単体テスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mind.core.context.recall_neighbors import expand_recall_neighbors
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.legacy_parse import chunk_diary_body
from mind.core.memory.store import MemoryStore


def test_expand_chunk_to_neighborhood_passage() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
        store = MemoryStore(str(tmp / "m.db"), embedder=emb, vector_dim=4)
        parent_body = (
            "冷蔵庫に卵があって、準備をした。"
            "オムライスを食べたんだ。"
            "大盛にしすぎて少し苦しかったな。"
            "そのあとお茶を飲んだ。"
        )
        # 中盤だけをチャンクに
        chunk = "オムライスを食べたんだ。"
        assert chunk in parent_body
        # 前後が十分つながるようパディング文字を確保
        parent_body = ("前置き。" * 20) + parent_body + ("あとがき。" * 20)
        pid = store.add_memory(
            parent_body, type="diary", protection_grade="A", embed=False
        )
        cid = store.add_memory(
            chunk, type="event", parent_id=pid, protection_grade="B", embed=True
        )
        pair = store.get_memory_by_id(cid)
        assert pair is not None
        rec, _ = pair
        out = expand_recall_neighbors(store, [rec], pad_chars=40)
        assert len(out) == 1
        text = out[0].content
        assert "オムライス" in text
        assert "卵" in text or "苦しかっ" in text
        assert len(text) > len(chunk)


def test_expand_uses_real_chunk_diary_body_output() -> None:
    """合成データではなく chunk_diary_body の実出力を使った round-trip。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
        store = MemoryStore(str(tmp / "m.db"), embedder=emb, vector_dim=4)
        parent_body = (
            "朝は肌寒くて、いつもより早く起きた。\n\n"
            "冷蔵庫に卵があって、オムライスを作ることにした。"
            "大盛にしすぎて少し苦しかったな。\n\n"
            "そのあとお茶を飲みながら、今日の予定を考えた。"
            "宮古島の海の写真をまた見返してしまった。\n\n"
            "夜は早めに休むことにした。明日も元気に過ごしたい。"
        )
        chunks = chunk_diary_body(parent_body, min_chars=10)
        assert len(chunks) >= 2
        target_chunk = next(c for c in chunks if "オムライス" in c)

        pid = store.add_memory(parent_body, type="diary", protection_grade="A", embed=False)
        cid = store.add_memory(
            target_chunk, type="event", parent_id=pid, protection_grade="B", embed=True
        )
        pair = store.get_memory_by_id(cid)
        assert pair is not None
        rec, _ = pair
        out = expand_recall_neighbors(store, [rec], pad_chars=40)
        assert len(out) == 1
        text = out[0].content
        assert "オムライス" in text
        # 実チャンク経由でも、パディングにより前後の文脈が拾えていること
        assert len(text) >= len(target_chunk)


def test_expand_dedup_preserves_list_length() -> None:
    """同一親から同じ近傍パッセージへ展開される2ヒットは、件数を減らさず後者を元チャンクのまま通す。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
        store = MemoryStore(str(tmp / "m.db"), embedder=emb, vector_dim=4)
        # 短い親本文にして pad_chars(40) が全域を覆うようにし、2チャンクの近傍窓を
        # 完全一致（= 親本文全体）へ収束させ、dedup 経路を確実に通す
        parent_body = "冷蔵庫に卵があって、オムライスを食べたんだ。"
        pid = store.add_memory(parent_body, type="diary", protection_grade="A", embed=False)
        # 同じ親・近い位置から2つのチャンクがヒットしたケース（同じ近傍パッセージに広がる）
        chunk_a = "冷蔵庫に卵があって、"
        chunk_b = "オムライスを食べたんだ。"
        cid_a = store.add_memory(chunk_a, type="event", parent_id=pid, protection_grade="B", embed=True)
        cid_b = store.add_memory(chunk_b, type="event", parent_id=pid, protection_grade="B", embed=True)
        pair_a = store.get_memory_by_id(cid_a)
        pair_b = store.get_memory_by_id(cid_b)
        assert pair_a is not None and pair_b is not None
        rec_a, _ = pair_a
        rec_b, _ = pair_b

        out = expand_recall_neighbors(store, [rec_a, rec_b], pad_chars=40)
        assert len(out) == 2  # 件数は維持される（重複しても捨てない）
        # 先に処理された方は近傍（=親本文全体）へ展開、後発は重複回避で元チャンクのまま通る
        assert out[0].content == parent_body
        assert out[1].content == chunk_b


def test_expand_leaves_orphan_cards() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
        store = MemoryStore(str(tmp / "m.db"), embedder=emb, vector_dim=4)
        mid = store.add_memory("高野漁港の約束", type="promise", protection_grade="S", embed=True)
        pair = store.get_memory_by_id(mid)
        assert pair is not None
        rec, _ = pair
        out = expand_recall_neighbors(store, [rec])
        assert out[0].content == "高野漁港の約束"
