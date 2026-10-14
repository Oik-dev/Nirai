"""埋め込みの再利用で想起を変えず、中断後の原文も同じ行に照合する。"""

from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import math
from types import SimpleNamespace

import pytest

from mind.core.chores.explore import append_explore, read_explore, research_record_text
from mind.core.chores.letters import append_line, read_lines
from mind.core.idea import Idea
from mind.core.memory.explore_sleep import prepare_pages
from mind.core.memory.index import IndexedPage, MemoryIndex, Passage, build_index
from mind.core.memory.lookup import Record, RecordLookup, _still_present
from mind.core.memory.page import load_pages, write_page
from mind.core.memory.recall import Cue, Recaller, load_recall_params


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone(timedelta(hours=9)))


def _index():
    pages = {
        name: IndexedPage(name, "diary", None, None, name, "", "body", "evidence",
                          5, None, "", "", True, frozenset(), "body")
        for name in ("one", "two")
    }
    passages = [
        Passage(name, "body", f"{name}-{letter}", vector)
        for name in pages
        for letter, vector in (("A", (1.0, 0.0)), ("B", (0.0, 1.0)),
                               ("C", (0.7071067811865475, 0.7071067811865476)))
    ]
    return MemoryIndex(pages, passages, {})


class _FormerPassageSelection(Recaller):
    """変更前の同一文の再埋め込みを、mockで比較する参照。"""

    def recall(self, cue, **kwargs):
        self._tested_cue = cue
        return super().recall(cue, **kwargs)

    def _best_passage(self, page):
        passages = [x for x in self.index.passages if x.page_id == page.id and x.role != "head"]
        if not passages:
            return page.body or page.evidence
        v = self.embed(self._tested_cue.utterance)
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return max(passages, key=lambda x: sum(a * b / norm for a, b in zip(v, x.vector))).text


@pytest.mark.parametrize("vector", [(3.0, 4.0), (1.0000000000000002, 1.0),
                                    (0.0, 0.0), (1e-200, -1e-200)])
@pytest.mark.parametrize("recent", [(), (("Master", "context"),)])
def test_reuse_preserves_complete_remembered_results_and_updates_each_cue(vector, recent):
    params = replace(load_recall_params(), threshold=-100, vivid_margin=0, noise=0, max_spontaneous=2)
    calls = {"new": [], "old": []}

    def embed_for(which):
        def embed(text):
            calls[which].append(text)
            if text == "cue-a":
                return list(vector)
            if text == "cue-b":
                return list(reversed(vector))
            return [0.0, 1.0]  # 履歴の手がかりも別に計算する。
        return embed

    current = Recaller(_index(), embed=embed_for("new"), params=params)
    former = _FormerPassageSelection(_index(), embed=embed_for("old"), params=params)
    for utterance in ("cue-a", "cue-b"):
        cue = Cue(utterance, recent, NOW)
        new_result = current.recall(cue)
        old_result = former.recall(cue)
        assert new_result == old_result
        assert len(new_result) == 2 and all(page.vivid for page in new_result)
        assert Counter(calls["new"])[utterance] == 1
        assert Counter(calls["old"])[utterance] == 3
    assert len(calls["new"]) == 2 * (1 + bool(recent))
    assert len(calls["old"]) == 2 * (3 + bool(recent))


def _record_after_torn_line(tmp_path, torn):
    idea = Idea(tmp_path)
    directory = idea.lifelog / "explore"
    stamp = (NOW - timedelta(days=1)).isoformat()
    append_explore(directory, {"kind": "explore-time", "ts": stamp, "thread": stamp,
                               "question": "Mocktides question", "kept": None})
    path = next(directory.glob("*.jsonl"))
    initial = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(torn)
    reading = {"kind": "explored", "time": stamp, "failed": None, "results": [],
               "thought": "Mocktides thought", "next": ""}
    ref = append_explore(directory, reading)
    record = Record(ref, datetime.fromisoformat(stamp), "Serina",
                    research_record_text("Mocktides question", reading))
    return idea, path, initial, reading, record


@pytest.mark.parametrize("torn", [
    b'{"kind": "explored", "time": "2026-10',
    b'{"kind": "explored", "thought": "\xe6',
])
def test_source_verification_recovers_torn_bytes_and_preserves_offset_and_line_number(tmp_path, torn):
    idea, path, initial, reading, record = _record_after_torn_line(tmp_path, torn)
    contents = path.read_bytes()
    offset = len(initial) + len(torn) + 1
    assert contents[:offset] == initial + torn + b"\n"
    assert json.loads(contents[offset:].splitlines()[0]) == reading
    assert record.ref.endswith("#3")
    assert [row.ref.rsplit("#", 1)[-1] for row in read_explore(path.parent)] == ["1", "3"]
    assert _still_present(idea.conversation, record)
    assert not _still_present(idea.conversation, replace(record, ref=record.ref.rsplit("#", 1)[0] + "#2"))
    assert not _still_present(idea.conversation, replace(record, text=record.text + "altered"))
    assert path.read_bytes() == contents


@pytest.mark.parametrize("broken", [b'{"kind": true,}\n', b"null\n", b"[]\n",
                                    b'{"kind": "\xff"}\n'])
@pytest.mark.parametrize("after_target", [False, True])
def test_completed_corruption_before_or_after_the_target_closes_verification(tmp_path, broken, after_target):
    idea, path, initial, _, record = _record_after_torn_line(tmp_path, b'{"kind": "explored", "time": "cut')
    if after_target:
        with path.open("ab") as stream:
            stream.write(broken)
    else:
        result = path.read_bytes().splitlines(keepends=True)[-1]
        path.write_bytes(initial + broken + result)
    with pytest.raises(ValueError):
        read_explore(path.parent)
    assert not _still_present(idea.conversation, record)


@pytest.mark.parametrize("deleted", ["origin", "result"])
def test_deleted_origin_or_result_is_not_verified(tmp_path, deleted):
    idea, path, _, _, record = _record_after_torn_line(tmp_path, b'{"kind": "explored", "time": "cut')
    rows = path.read_bytes().splitlines(keepends=True)
    number = 0 if deleted == "origin" else 2
    row = json.loads(rows[number])
    row["deleted"] = True
    rows[number] = (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(b"".join(rows))
    assert not _still_present(idea.conversation, record)


def test_a_later_attempt_cannot_supply_the_origin_of_an_earlier_reading(tmp_path):
    idea = Idea(tmp_path)
    directory = idea.lifelog / "explore"
    stamp = NOW.isoformat()
    reading = {"kind": "explored", "time": stamp, "failed": None, "results": [],
               "thought": "Mocktides thought", "next": ""}
    ref = append_explore(directory, reading)
    append_explore(directory, {"kind": "explore-time", "ts": stamp,
                               "question": "Mocktides question", "kept": None})
    record = Record(ref, NOW, "Serina", research_record_text("Mocktides question", reading))
    assert not _still_present(idea.conversation, record)


def test_recovered_explore_source_is_available_through_rebuilt_lookup(tmp_path):
    idea, path, _, _, record = _record_after_torn_line(tmp_path, b'{"kind": "explored", "time": "cut')
    assert prepare_pages(SimpleNamespace(idea=idea), before=NOW, signed="mock") == 1
    page = load_pages(idea.memory)[0]
    write_page(idea.memory, page.with_words(title="Mocktides", gist="Mocktides gist", importance=5,
                                            written_by="mock", body="Mocktides thought"))
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=lambda text: [1.0, 0.0], model="mock", progress=lambda text: None)
    recaller = Recaller(MemoryIndex.load(idea.memory_index), embed=lambda text: [1.0, 0.0])
    cue = Cue("Mocktides", (), NOW)
    recaller.recall(cue)
    lookup = RecordLookup(idea.memory_index, idea.conversation)
    assert any(row.ref == record.ref for row in lookup.search(cue, recaller, limit=8)[0])
    assert any(row.ref == record.ref for row in lookup.evidence(page.id, cue, recaller, limit=8))



def _letter_after_torn_line(tmp_path, torn):
    idea = Idea(tmp_path)
    directory = idea.lifelog / "letters"
    append_line(directory, {"kind": "letter-time", "ts": NOW.isoformat(), "thought": "first"})
    path = next(directory.glob("*.jsonl"))
    initial = path.read_bytes()
    with path.open("ab") as stream:
        stream.write(torn)
    append_line(directory, {"kind": "letter-time", "ts": (NOW + timedelta(seconds=1)).isoformat(),
                            "thought": "Mock thought", "tell": "Mock tell",
                            "write": {"to": "Holo", "text": "Mock letter"}})
    record = Record(f"lifelog/letters/{path.name}#3", NOW, "Serina",
                    "Mock thought\nMock tell\nMock letter")
    return idea, path, initial, record


@pytest.mark.parametrize("torn", [
    b'{"v": 1, "kind": "letter-time", "thought": "cut',
    b'{"v": 1, "kind": "letter-time", "thought": "\xe6',
])
def test_new_letter_source_verifies_after_utf8_or_json_cut_without_changing_bytes(tmp_path, torn):
    idea, path, initial, record = _letter_after_torn_line(tmp_path, torn)
    contents = path.read_bytes()
    offset = len(initial) + len(torn) + 1
    assert contents[:offset] == initial + torn + b"\n"
    assert json.loads(contents[offset:].splitlines()[0])["thought"] == "Mock thought"
    assert [row.number for row in read_lines(path.parent)] == [1, 3]
    assert _still_present(idea.conversation, record)
    assert not _still_present(idea.conversation, replace(record, ref=record.ref.rsplit("#", 1)[0] + "#2"))
    assert not _still_present(idea.conversation, replace(record, text=record.text + "altered"))
    assert path.read_bytes() == contents


@pytest.mark.parametrize("torn", [
    b'{"kind": "letter-time", "thought": "cut',
    b'{"kind": "letter-time", "thought": "\xe6',
    b'{"v": ',
])
def test_legacy_letter_cut_and_unfinished_format_stamp_still_close_source_verification(tmp_path, torn):
    idea, path, _, record = _letter_after_torn_line(tmp_path, torn)
    with pytest.raises(ValueError):
        read_lines(path.parent)
    assert not _still_present(idea.conversation, record)


@pytest.mark.parametrize("broken", [b'{"v": 1, "kind": true,}\n', b"null\n", b"[]\n",
                                    b'{"v": 1, "thought": "\xff"}\n'])
@pytest.mark.parametrize("after_target", [False, True])
def test_completed_letter_corruption_before_or_after_target_closes_verification(tmp_path, broken, after_target):
    idea, path, initial, record = _letter_after_torn_line(tmp_path, b'{"v": 1, "thought": "cut')
    if after_target:
        with path.open("ab") as stream:
            stream.write(broken)
    else:
        result = path.read_bytes().splitlines(keepends=True)[-1]
        path.write_bytes(initial + broken + result)
    with pytest.raises(ValueError):
        read_lines(path.parent)
    assert not _still_present(idea.conversation, record)


@pytest.mark.parametrize("kind", ["letter-time", "letter"])
def test_complete_legacy_letters_and_blank_line_keep_original_verification(tmp_path, kind):
    idea = Idea(tmp_path)
    name = "letters" if kind == "letter-time" else "post"
    directory = idea.lifelog / name
    directory.mkdir(parents=True)
    path = directory / "2026-10.jsonl"
    row = ({"kind": kind, "ts": NOW.isoformat(), "thought": "Mock thought", "tell": "",
            "write": {"text": "Mock body"}} if kind == "letter-time"
           else {"kind": kind, "ts": NOW.isoformat(), "body": "Mock body", "from": "Holo"})
    contents = b"\n" + (json.dumps(row) + "\n").encode("utf-8")
    path.write_bytes(contents)
    record = Record(f"lifelog/{name}/{path.name}#2", NOW, "Serina" if kind == "letter-time" else "Holo",
                    "Mock thought\n\nMock body" if kind == "letter-time" else "Mock body")
    assert [line.number for line in read_lines(directory)] == [2]
    assert _still_present(idea.conversation, record)
    assert path.read_bytes() == contents
