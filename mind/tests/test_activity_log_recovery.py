"""記録の中断から戻っても、送信の重複と検索回数の抜けを作らない。"""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from mind.core import lifelog
from mind.core.chores.explore import append_explore, attempts_today, read_explore, research_once
from mind.core.chores.letters import append_line, can_send_again, read_lines


NOW = datetime(2026, 10, 14, 12, tzinfo=timezone(timedelta(hours=9)))


def test_every_byte_cut_of_json_dictionary_is_readable_before_or_after_closing_the_line():
    row = {"kind": "explore-time", "ts": NOW.isoformat(), "question": '海と"潮"\\改行\n\u0001',
           "kept": None, "shown": {"yes": True, "no": False, "number": -1.23e-20}, "items": [1, "波"]}
    encoded = json.dumps(row, ensure_ascii=False).encode("utf-8")
    for stop in range(1, len(encoded)):
        prefix = encoded[:stop]
        assert lifelog._read_jsonl_object(prefix, "test#1") is None, stop
        assert lifelog._read_jsonl_object(prefix + b"\n", "test#1") is None, stop
    assert lifelog._read_jsonl_object(encoded, "test#1") == row


@pytest.mark.parametrize("raw", [
    b"null", b"[]", b'""', b"false", b"42",
    b'{"kind": invalid}', b'{"kind": true,}', b'{"kind": "value"}extra',
    b'{"kind": "bad\\q', b'{"kind": "\\uX',
    b'{"x": "quoted"e', b'{"x": truee',
    b'{"kind": "\xff"}', b'{"kind": \xe6',
])
def test_bad_dictionary_or_non_dictionary_closes_the_gate(raw):
    for ending in (b"", b"\n"):
        with pytest.raises(ValueError, match="test#7"):
            lifelog._read_jsonl_object(raw + ending, "test#7")


def test_letters_keep_completed_intent_and_torn_bytes_when_appending_again(tmp_path):
    directory = tmp_path / "letters"
    intent = {"kind": "letter-time", "ts": NOW.isoformat(), "read": [],
              "write": {"to": "Holo", "text": "こんにちは"}, "kept": None}
    append_line(directory, intent)
    path = next(directory.glob("*.jsonl"))
    original = path.read_bytes()
    assert original.startswith(b'{"v": 1,')
    # postedが切れても、確定したletter-timeは同日の再送を止める。
    torn = b'{"v": 1, "kind": "posted", "time": "2026-10'
    with path.open("ab") as stream:
        stream.write(torn)
    assert not can_send_again("Holo", NOW, [], read_lines(directory), day_of=lambda at: at.date())
    append_line(directory, {"kind": "letter-time", "ts": (NOW + timedelta(minutes=1)).isoformat(),
                            "read": [], "write": None, "kept": None})
    assert path.read_bytes().startswith(original + torn + b"\n")
    rows = read_lines(directory)
    assert [line.number for line in rows] == [1, 3]
    assert rows[0].value["ts"] == intent["ts"]
    assert not can_send_again("Holo", NOW, [], rows, day_of=lambda at: at.date())


def test_new_letter_intent_cut_in_japanese_recovers_without_changing_its_bytes(tmp_path):
    directory = tmp_path / "letters"
    append_line(directory, {"kind": "letter-time", "ts": NOW.isoformat(), "thought": "海"})
    path = next(directory.glob("*.jsonl"))
    encoded = path.read_bytes()
    cut = encoded.index("海".encode("utf-8")) + 1
    torn = encoded[:cut]
    path.write_bytes(torn)
    assert read_lines(directory) == []
    append_line(directory, {"kind": "letter-time", "ts": NOW.isoformat(), "thought": "次"})
    assert path.read_bytes().startswith(torn + b"\n")
    assert [line.number for line in read_lines(directory)] == [2]


@pytest.mark.parametrize("torn", [
    b'{"kind": "letter-time", "ts": "2026-10',
    b'{"kind": "posted", "time": "2026-10',
    b'{"v": ',
])
def test_legacy_letter_or_incomplete_format_stamp_stays_closed(tmp_path, torn):
    directory = tmp_path / "letters"
    directory.mkdir()
    (directory / "2026-10.jsonl").write_bytes(torn)
    with pytest.raises(ValueError, match="2026-10.jsonl#1"):
        read_lines(directory)


@pytest.mark.parametrize("directory_name,read", [("letters", read_lines), ("post", read_lines),
                                                ("body", read_lines), ("explore", read_explore)])
@pytest.mark.parametrize("broken", [b'{"v": 1, "kind": true,}\n', b"null\n", b"[]\n"])
def test_completed_bad_rows_fail_for_every_caller(tmp_path, directory_name, read, broken):
    directory = tmp_path / directory_name
    directory.mkdir()
    (directory / "2026-10.jsonl").write_bytes(broken)
    with pytest.raises(ValueError, match="2026-10.jsonl#1"):
        read(directory)


def test_research_result_torn_after_six_attempts_does_not_allow_a_seventh_search(tmp_path):
    directory = tmp_path / "explore"
    for offset in range(6):
        append_explore(directory, {"kind": "explore-time", "ts": (NOW + timedelta(seconds=offset)).isoformat(),
                                   "question": "海", "kept": None, "thread": str(offset)})
    path = next(directory.glob("*.jsonl"))
    original = path.read_bytes()
    torn = b'{"kind": "explored", "time": "2026-10'
    with path.open("ab") as stream:
        stream.write(torn)
    called = []
    skill = SimpleNamespace(enabled=True, search=lambda query: called.append(query))
    assert research_once(directory, at=NOW + timedelta(minutes=1), question="別の問い", brain="Gemini",
                         question_location="cloud", reading_location="cloud", shown=None, follows=None,
                         thread=None, marks=None, judge=None, skill=skill, read=lambda q, rows: {}) is None
    assert called == []
    assert attempts_today(read_explore(directory), NOW) == 6
    ref = append_explore(directory, {"kind": "explored", "time": NOW.isoformat(),
                                    "failed": "interrupted", "results": [], "thought": "", "next": ""})
    assert ref.endswith("#8")
    assert path.read_bytes().startswith(original + torn + b"\n")
    assert [line.ref.rsplit("#", 1)[-1] for line in read_explore(directory)] == ["1", "2", "3", "4", "5", "6", "8"]
    assert attempts_today(read_explore(directory), NOW) == 6


def test_research_attempt_torn_before_search_recovers_then_records_a_new_attempt(tmp_path):
    directory = tmp_path / "explore"
    directory.mkdir()
    path = directory / "2026-10.jsonl"
    torn = b'{"kind": "explore-time", "ts": "2026-10'
    path.write_bytes(torn)
    called = []

    def search(query):
        assert attempts_today(read_explore(directory), NOW) == 1
        called.append(query)
        return SimpleNamespace(results=[])

    result = research_once(directory, at=NOW, question="海", brain="Gemini",
                           question_location="cloud", reading_location="cloud", shown=None, follows=None,
                           thread=None, marks=None, judge=None, skill=SimpleNamespace(enabled=True, search=search),
                           read=lambda q, rows: {"thought": "", "next": ""})
    assert called == ["海"] and result["attempt"].endswith("#2")
    assert path.read_bytes().startswith(torn + b"\n")
    assert [line.value["kind"] for line in read_explore(directory)] == ["explore-time", "explored"]


def test_research_stays_inside_when_fsync_fails(tmp_path, monkeypatch):
    called = []

    def fail_sync(fd):
        raise OSError("disk unavailable")

    monkeypatch.setattr(lifelog.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="disk unavailable"):
        research_once(tmp_path / "explore", at=NOW, question="海", brain="Gemini",
                      question_location="cloud", reading_location="cloud", shown=None, follows=None,
                      thread=None, marks=None, judge=None,
                      skill=SimpleNamespace(enabled=True, search=lambda query: called.append(query)),
                      read=lambda q, rows: {})
    assert called == []
