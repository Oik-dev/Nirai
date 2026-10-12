"""Serinaの手紙を二重に読まず、家の文を外に出さないこと。"""

from datetime import datetime, timezone
from types import SimpleNamespace
import json
import sqlite3
from contextlib import closing

from mind.core.chores.letters import (
    append_line, can_send_again, current_activity, home_clear_to_send,
    last_letter_time, needs_letter_time, pending_letters, read_lines, reply_to,
    today_letters,
)
from mind.core.routing.home_marks import HomeMarks
from mind.core.idea import Idea
from mind.core.config import load_thresholds
from mind.core.feeling.appraisal import parse_activity_appraisal
from mind.core.feeling.feelings import Feelings
from mind.core.lifelog import FeelingLog
from mind.core.memory.letter_sleep import prepare_pages, write_letter_page
from mind.core.memory.page import load_pages, write_page
from mind.core.memory.index import build_index
from mind.core.memory.lookup import Record, _still_present
from mind.core.state.serina_day import serina_day_id
from mind.core.chores import post_client
from mind.core.chores.post_client import PostConfig
from types import SimpleNamespace


def at(ts):
    return datetime.fromisoformat(ts)


def day(dt):
    return dt.date().isoformat()


def test_recovery_and_reselection(tmp_path):
    post_dir = tmp_path / "post"
    own_dir = tmp_path / "letters"
    body_dir = tmp_path / "body"
    post_dir.mkdir()
    body_dir.mkdir()
    post = {"kind": "letter", "ts": "2026-10-12T01:00:00+00:00", "id": "A", "from": "Holo", "to": "Serina", "work": "serina", "body": "こんにちは"}
    (post_dir / "day.jsonl").write_text(json.dumps(post, ensure_ascii=False) + "\n", encoding="utf-8")
    assert [r.value["id"] for r in pending_letters(read_lines(post_dir), read_lines(own_dir))[0]] == ["A"]
    append_line(own_dir, {"kind": "letter-time", "ts": "2026-10-12T01:01:00+00:00", "read": ["A"], "thought": "嬉しい", "tell": "", "write": None, "kept": None, "brain": "gemma"})
    assert pending_letters(read_lines(post_dir), read_lines(own_dir))[0] == []
    assert [r.value["id"] for r in pending_letters(read_lines(post_dir), read_lines(own_dir))[1]] == ["A"]
    assert last_letter_time(read_lines(own_dir)) == at("2026-10-12T01:01:00+00:00")
    assert reply_to("Holo", read_lines(post_dir)) == "A"
    assert not needs_letter_time(activity="手紙を書く", last_time=at("2026-10-12T01:01:00+00:00"), selected_at=at("2026-10-12T01:00:00+00:00"), unread_count=0)
    assert needs_letter_time(activity="手紙を書く", last_time=at("2026-10-12T01:01:00+00:00"), selected_at=at("2026-10-12T01:02:00+00:00"), unread_count=0)
    assert not needs_letter_time(activity="砂地で休む", last_time=None, selected_at=at("2026-10-12T01:02:00+00:00"), unread_count=1)
    (body_dir / "log.jsonl").write_text(json.dumps({"kind": "activity", "value": "手紙を書く"}), encoding="utf-8")
    assert current_activity(read_lines(body_dir)) == "手紙を書く"


def test_gate_for_same_day_reply_next_day_and_unknown_send(tmp_path):
    post, own = tmp_path / "post", tmp_path / "own"
    write = {"kind": "letter-time", "ts": "2026-10-12T01:00:00+00:00", "brain": "gemini", "read": [],
             "thought": "", "tell": "", "write": {"to": "Holo", "text": "質問", "reply_to": None}, "kept": None}
    append_line(own, write)
    assert not can_send_again("Holo", at("2026-10-12T03:00:00+00:00"), read_lines(post), read_lines(own), day_of=day)
    # posted行がなくても再送しない。明示的な送信失敗のみ門を解除する。
    append_line(post, {"kind": "letter", "ts": "2026-10-12T02:00:00+00:00", "id": "B", "to": "Serina", "from": "Holo", "body": "答え", "work": "serina"})
    assert can_send_again("Holo", at("2026-10-12T03:00:00+00:00"), read_lines(post), read_lines(own), day_of=day)
    assert can_send_again("Holo", at("2026-10-13T03:00:00+00:00"), [], read_lines(own), day_of=day)
    append_line(own, {"kind": "posted", "time": write["ts"], "failed": "connection refused"})
    assert can_send_again("Holo", at("2026-10-12T03:00:00+00:00"), [], read_lines(own), day_of=day)


def test_home_marks_fail_closed(tmp_path):
    marks = HomeMarks(tmp_path)
    assert not home_clear_to_send("local", "外に出せない。", "", "", marks, None)
    assert not home_clear_to_send("local", "外に出せない。", "", "", marks, lambda parts: (_ for _ in ()).throw(RuntimeError("judge failed")))
    assert not home_clear_to_send("local", "外に出せない。", "", "", marks, lambda parts: [1])
    assert home_clear_to_send("local", "普通の手紙。", "", "", marks, lambda parts: [])
    assert home_clear_to_send("cloud", "何でもない手紙。", "", "", marks, lambda parts: (_ for _ in ()).throw(AssertionError("judge must not run")))


def test_letter_memory_sources_reach_original_lines_and_search_index(tmp_path):
    idea = Idea(tmp_path)
    post = idea.lifelog / "post"
    post.mkdir(parents=True)
    (post / "2026-10-11.jsonl").write_text(json.dumps({
        "kind": "letter", "ts": "2026-10-11T03:00:00+00:00", "from": "Holo", "to": "Serina",
        "id": "A", "work": "serina", "body": "新しい星を眺めたよ。",
    }, ensure_ascii=False) + "\n", encoding="utf-8")
    append_line(idea.lifelog / "letters", {
        "kind": "letter-time", "ts": "2026-10-11T03:05:00+00:00", "brain": "gemma",
        "read": ["A"], "thought": "星がきれい。", "tell": "", "write": None, "kept": None,
    })
    own_line = read_lines(idea.lifelog / "letters")[0]
    feeling_source = f"lifelog/letters/{own_line.path.name}#{own_line.number}"
    feelings = Feelings(FeelingLog(idea.lifelog / "feeling"), load_thresholds().feeling)
    feelings.feel_activity(parse_activity_appraisal({
        "feeling": "星の話が楽しい", "valence": "とてもうれしい", "arousal": "大きく動いた",
    }), source=feeling_source, at=at("2026-10-11T03:05:02+00:00"))
    memory = SimpleNamespace(idea=idea)
    before = at("2026-10-13T01:00:00+00:00")
    assert prepare_pages(memory, before=before, signed="Gemma", feelings=feelings) == 1
    assert prepare_pages(memory, before=before, signed="Gemma", feelings=feelings) == 0
    page = load_pages(idea.memory)[0]
    assert page.affect is not None and page.affect["valence"] > 0
    assert len(page.source) == 2
    assert all(ref.startswith("lifelog/") and "#1" in ref for ref in page.source)
    assert all((idea.root / ref.split("#")[0]).is_file() for ref in page.source)
    def answer(prompt, schema, temperature):
        assert "星がきれい" in prompt and "新しい星を眺めた" in prompt
        return {"title": "Holoと星の話", "gist": "星の話をした", "story": "わたしはHoloから星の話を聞いた。",
                "importance": 4, "concepts": ["星", "Holo"]}
    written = write_letter_page(idea, page, persona="Serina", ask=answer, signed="Gemma")
    assert written.concepts == ("手紙", "Holo", "星")
    write_page(idea.memory, written)
    assert written.written
    target = idea.memory_index
    build_index(idea.memory, idea.conversation, "Serina", target, embed=lambda text: [0.1, 0.2], model="test", progress=lambda text: None)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT own_joy FROM pages WHERE id=?", (page.id,)).fetchone()[0] > 0
        rows = conn.execute("SELECT ref, speaker, text FROM record_fts").fetchall()
        assert len(rows) == 2
        assert {row[1] for row in rows} == {"Holo", "Serina"}
        refs = [row[0] for row in conn.execute("SELECT ref FROM page_sources WHERE page_id=?", (page.id,))]
        assert set(refs) == set(page.source)
    for ref, speaker, text in rows:
        assert _still_present(idea.conversation, Record(ref, at("2026-10-11T03:00:00+00:00"), speaker, text))


def test_unread_letter_cannot_become_memory_or_index_until_read_even_after_day_change(tmp_path):
    idea = Idea(tmp_path)
    arrived = "2026-10-10T03:00:00+00:00"
    read_at = "2026-10-12T03:05:00+00:00"
    append_line(idea.lifelog / "post", {"kind": "letter", "ts": arrived,
                                        "id": "WAIT", "from": "Holo", "to": "Serina",
                                        "work": "serina", "body": "未読の秘密の話。"})
    memory = SimpleNamespace(idea=idea)
    before = at("2026-10-13T01:00:00+00:00")
    assert prepare_pages(memory, before=before, signed="Gemma") == 0
    assert load_pages(idea.memory) == []
    early = today_letters(read_lines(idea.lifelog / "post"), [], now=at(read_at),
                          day_of=lambda d: serina_day_id(d).isoformat())
    assert early["received"] == [] and early["notice"] == "Holoから1通"
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=lambda text: [0.1, 0.2], model="test", progress=lambda _: None)
    with closing(sqlite3.connect(idea.memory_index)) as conn:
        assert conn.execute("SELECT count(*) FROM record_fts").fetchone()[0] == 0
    append_line(idea.lifelog / "letters", {"kind": "letter-time", "ts": read_at,
                                            "brain": "gemma", "read": ["WAIT"],
                                            "thought": "読んで初めて知った。", "tell": "",
                                            "write": None, "kept": None})
    assert prepare_pages(memory, before=before, signed="Gemma") == 1
    page = load_pages(idea.memory)[0]
    assert page.id == f"letter-{serina_day_id(at(read_at)).isoformat()}-Holo"
    assert page.start == at(read_at) and len(page.source) == 2
    late = today_letters(read_lines(idea.lifelog / "post"), read_lines(idea.lifelog / "letters"),
                         now=at(read_at), day_of=lambda d: serina_day_id(d).isoformat())
    assert len(late["received"]) == 1 and late["notice"] == ""
    def answer(prompt, schema, temperature):
        assert "未読の秘密の話" in prompt and "読んで初めて知った" in prompt
        return {"title": "手紙を読んだ日", "gist": "Holoの話を読んだ", "story": "わたしはHoloの手紙を読んだ。",
                "importance": 4, "concepts": ["秘密の話"]}
    write_page(idea.memory, write_letter_page(idea, page, persona="", ask=answer, signed="Gemma"))
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=lambda text: [0.1, 0.2], model="test", progress=lambda _: None)
    with closing(sqlite3.connect(idea.memory_index)) as conn:
        assert conn.execute("SELECT count(*) FROM record_fts").fetchone()[0] == 2
        evidence = conn.execute("SELECT evidence FROM pages WHERE id=?", (page.id,)).fetchone()[0]
        assert "未読の秘密の話" in evidence
        assert conn.execute("SELECT count(*) FROM passages WHERE page_id=? AND role='record'", (page.id,)).fetchone()[0] > 0


def test_reply_inherits_original_work_without_overriding_it(monkeypatch):
    args = []
    async def fake_call(config, name, arguments):
        args.append((name, arguments))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="L20261012-102030-abc012")], isError=False)
    monkeypatch.setattr(post_client, "_call", fake_call)
    config = PostConfig("http://localhost:1/mcp/Serina", ("Holo",), "serina")
    assert post_client.send_letter(config, to="Holo", body="こんにちは").letter_id
    assert args[-1] == ("send_letter", {"to": "Holo", "body": "こんにちは", "work": "serina"})
    assert post_client.send_letter(config, to="Holo", body="お返事", reply_to="L20261012-070000-bbbbbb").letter_id
    assert args[-1] == ("send_letter", {"to": "Holo", "body": "お返事", "reply_to": "L20261012-070000-bbbbbb"})
