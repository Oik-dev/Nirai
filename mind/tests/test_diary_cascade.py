"""日記削除カスケード: 原典は巻き込まない。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.memory.diary_cascade import collect_diary_material_targets
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.store import MemoryStore


def _store() -> MemoryStore:
    embedder = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    return MemoryStore(str(Path(tempfile.mkdtemp()) / "mem.db"), embedder=embedder, vector_dim=4)


def _set_source(store: MemoryStore, memory_id: int, source: str) -> None:
    conn = store._connect()  # noqa: SLF001
    try:
        conn.execute("UPDATE memories SET source = ? WHERE id = ?", (source, memory_id))
        conn.commit()
    finally:
        conn.close()


def _set_created_at(store: MemoryStore, memory_id: int, created_at: str) -> None:
    conn = store._connect()  # noqa: SLF001
    try:
        conn.execute("UPDATE memories SET created_at = ? WHERE id = ?", (created_at, memory_id))
        conn.commit()
    finally:
        conn.close()


def test_cascade_skips_inherited_source_even_in_window() -> None:
    store = _store()
    distilled = store.add_memory("今日の蒸留", type="fact", importance=0.5, protection_grade="B")
    inherited = store.add_memory("原典の一文", type="fact", importance=0.5, protection_grade="B")
    diary_id = store.add_memory("今日の日記", type="episodic", importance=0.5, protection_grade="A")
    _set_source(store, inherited, "継承記憶r1.md")
    # 同じ窓に入るよう created_at を揃える
    base = "2026-07-21T10:00:00+09:00"
    mid = "2026-07-21T11:00:00+09:00"
    end = "2026-07-21T12:00:00+09:00"
    _set_created_at(store, distilled, base)
    _set_created_at(store, inherited, mid)
    _set_created_at(store, diary_id, end)

    diary, _ = store.get_memory_by_id(diary_id)  # type: ignore[misc]
    assert diary is not None
    targets = collect_diary_material_targets(store, diary)
    ids = {t.id for t in targets}
    assert distilled in ids
    assert inherited not in ids


def test_cascade_empty_for_non_diary() -> None:
    store = _store()
    mid = store.add_memory("fact", type="fact", importance=0.5)
    record, _ = store.get_memory_by_id(mid)  # type: ignore[misc]
    assert collect_diary_material_targets(store, record) == []
