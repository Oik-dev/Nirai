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

from serina.core.context.recall_neighbors import expand_recall_neighbors
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore


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
