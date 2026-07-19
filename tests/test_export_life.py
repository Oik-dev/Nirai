"""life/ 一方向生成（A10）のスモークテスト。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from tools.export_life import export_life


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda _m, _t: [1.0, 0.0, 0.0, 0.0])


def test_export_life_from_temp_db() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        db_path = tmp / "test_memory.db"
        life_dir = tmp / "life"
        summaries_path = tmp / "summaries" / "blocks.json"
        summaries_path.parent.mkdir(parents=True)
        summaries_path.write_text(
            json.dumps(
                {
                    "prefs_summary": {"content": "甘いものが好き"},
                    "relation_summary": {"content": "最近は穏やか"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        store = MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)
        mem_id = store.add_memory("散歩が好き", type="fact")
        store.facts.add_fact(
            subject="master",
            predicate="likes",
            object="walk",
            statement="マスターは散歩が好き",
            episode_ids=[mem_id],
        )

        written = export_life(db_path, life_dir, summaries_path=summaries_path)
        assert len(written) >= 4
        index_text = (life_dir / "index.md").read_text(encoding="utf-8")
        assert "記憶件数: 1" in index_text
        facts_text = (life_dir / "facts.md").read_text(encoding="utf-8")
        assert "散歩が好き" in facts_text
        summaries_text = (life_dir / "summaries.md").read_text(encoding="utf-8")
        assert "甘いものが好き" in summaries_text
