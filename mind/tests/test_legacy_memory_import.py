"""legacy パーサと import（テンポラリDB）のテスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.legacy_parse import (
    chunk_diary_body,
    parse_diary_file,
    strip_ornament,
)
from mind.core.memory.store import MemoryStore
from mind.tools.import_legacy_memories import import_legacy_into_store


_DIARY_TEXT = """📖 セリナの日記 - 2025年3月08日

マスターと約束した。
絵文字🎉は消える。

📖 記録完了

📖 セリナの日記 - 2025年3月09日 22時
「タイトル行」

今日は長い本文を書く。記憶の継承について話した。
もう一文足してチャンクできるようにする。
"""


def _stub_store(tmp: Path) -> MemoryStore:
    emb = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    return MemoryStore(str(tmp / "mem.db"), embedder=emb, vector_dim=4)


def test_strip_ornament_removes_emoji_and_md() -> None:
    raw = "📖 今日は**大事な**日 😊\n```json\n{\"a\":1}\n```\n本文です"
    out = strip_ornament(raw)
    assert "📖" not in out
    assert "😊" not in out
    assert "**" not in out
    assert "json" not in out.lower() or "本文" in out
    assert "大事な" in out
    assert "本文です" in out


def test_parse_diary_fixture(tmp_path: Path) -> None:
    text = _DIARY_TEXT
    path = tmp_path / "d.txt"
    path.write_text(text, encoding="utf-8")
    entries = parse_diary_file(path)
    assert len(entries) == 2
    assert entries[0].date_iso.startswith("2025-03-08")
    assert "🎉" not in entries[0].body
    assert "約束" in entries[0].body
    assert "継承" in entries[1].body


def test_chunk_diary_merges_short() -> None:
    body = "短い1\n\n短い2\n\n" + ("長い段落です。" * 10)
    chunks = chunk_diary_body(body, min_chars=80)
    assert all(len(c) >= 40 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(body) - 20


def test_import_into_temp_db() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        legacy_root = tmp / "legacy"
        (legacy_root / "記憶").mkdir(parents=True)
        (legacy_root / "記憶" / "セリナの日記 - 20250308.txt").write_text(_DIARY_TEXT, encoding="utf-8")
        store = _stub_store(tmp)
        stats = import_legacy_into_store(store, legacy_root=legacy_root)
        assert stats.diary_parents == 2
        assert stats.diary_chunks >= stats.diary_parents
        # 親は vec に載らない
        conn = store._connect()  # noqa: SLF001
        try:
            parents = conn.execute(
                "SELECT id FROM memories WHERE type='episodic' AND parent_id IS NULL"
            ).fetchall()
            assert parents
            for (pid,) in parents:
                in_vec = conn.execute(
                    "SELECT 1 FROM memory_vec WHERE memory_id=?", (pid,)
                ).fetchone()
                assert in_vec is None
            child = conn.execute(
                "SELECT id FROM memories WHERE parent_id IS NOT NULL LIMIT 1"
            ).fetchone()
            assert child
            assert conn.execute(
                "SELECT 1 FROM memory_vec WHERE memory_id=?", (child[0],)
            ).fetchone()
        finally:
            conn.close()
