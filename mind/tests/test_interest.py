"""The traces belong to pages, not to a mutable interest counter."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from mind.core.memory.index import IndexedPage, MemoryIndex, build_index
from mind.core.memory.interest import InterestParams, interests, joy_weight
from mind.core.memory.page import Page, dumps, loads, write_page


NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)


def _index_page(id: str, concepts: set[str], *, master: float = 0.0, own: float = 0.0,
                days: int = 1) -> IndexedPage:
    return IndexedPage(
        id=id, kind="episode", start=NOW - timedelta(days=days, hours=1),
        end=NOW - timedelta(days=days), title=id, gist="", body="", evidence="",
        importance=5, arousal=0.5, prev="", next="", written=True,
        concepts=frozenset(concepts), searchable=id,
        master_heat=master, own_joy=own,
    )


def test_heat_is_only_written_once_and_other_fields_are_unchanged() -> None:
    page = Page(
        id="ep-1", kind="episode", start=NOW, end=NOW,
        source=("conversation/2026-10-12.jsonl#1",), concepts=("海",),
        title="元の題", gist="元の要点", importance=8, written_by="Gemma",
        body="元の本文\nそのまま。", later=({"day": "2026-10-12", "text": "考え直した"},),
    )
    labeled = page.with_heat("いいえ")
    assert replace(labeled, heat=None) == page
    assert loads(dumps(labeled)) == labeled
    with pytest.raises(ValueError):
        labeled.with_heat("とても")
    with pytest.raises(ValueError):
        page.with_heat("maybe")


def test_master_and_own_joy_and_rarity_are_derived_from_index() -> None:
    pages = {
        "1": _index_page("1", {"マスター", "海", "潮流"}, master=1),
        "2": _index_page("2", {"マスター", "星"}, own=0.8),
        "3": _index_page("3", {"マスター", "星"}),
    }
    top, neighbors = interests(MemoryIndex(pages, [], {}), NOW, InterestParams(top=2, neighbors=3))
    assert [entry.name for entry in top] == ["海", "潮流"] or [entry.name for entry in top] == ["潮流", "海"]
    assert all(entry.source == "マスターが夢中" for entry in top)
    assert "マスター" not in [entry.name for entry in top]
    assert "マスター" in neighbors  # only remaining neighbor of the winning episode
    assert interests(MemoryIndex(dict(pages), [], {}), NOW) == interests(MemoryIndex(pages, [], {}), NOW)
    without_deleted = {pid: page for pid, page in pages.items() if pid != "1"}
    after, _ = interests(MemoryIndex(without_deleted, [], {}), NOW)
    assert "海" not in [entry.name for entry in after]


def test_activity_joy_uses_only_activity_sources() -> None:
    affect = {"valence": 0.8, "arousal": 0.5}
    assert joy_weight(affect, ("lifelog/explore/2026-10.jsonl#2",)) == pytest.approx(0.6)
    assert joy_weight(affect, ("lifelog/post/2026-10.jsonl#4",)) == pytest.approx(0.6)
    assert joy_weight(affect, ("conversation/2026-10-12.jsonl#1",)) == 0
    assert joy_weight({"valence": -0.2, "arousal": 1}, ("lifelog/explore/2026-10.jsonl#2",)) == 0


def test_rebuilding_index_preserves_traces_and_ignores_deleted_pages(tmp_path) -> None:
    memory_dir = tmp_path / "memory"
    conversation_dir = tmp_path / "conversation"
    conversation_dir.mkdir()
    target = tmp_path / "index.db"
    for id, concept, heat, source in (
        ("ep-1", "海", "とても", ()),
        ("ep-2", "星", "いいえ", ("lifelog/explore/2026-10.jsonl#1",)),
        ("ep-3", "マスター", "いいえ", ()),
    ):
        page = Page(id=id, kind="episode", start=NOW - timedelta(days=1), end=NOW,
                    source=source, concepts=(concept, "マスター"), title=id, gist=id,
                    importance=5, affect={"valence": 0.8, "arousal": 0.5},
                    written_by="Gemma", body="書いた本文", heat=heat)
        write_page(memory_dir, page)
    def rebuild():
        build_index(memory_dir, conversation_dir, "Serina", target, embed=lambda text: [1.0, 0.0],
                    model="fake", progress=lambda line: None)
        return MemoryIndex.load(target)
    first = rebuild()
    assert first.pages["ep-1"].master_heat == 1.0
    assert first.pages["ep-2"].own_joy == pytest.approx(0.6)
    assert interests(rebuild(), NOW) == interests(first, NOW)
    (memory_dir / "episodes" / "2026" / "ep-1.md").unlink()
    after = rebuild()
    assert "ep-1" not in after.pages
    assert "海" not in [entry.name for entry in interests(after, NOW)[0]]
