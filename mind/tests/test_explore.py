from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
from contextlib import closing

from mind.core.chores.explore import (
    append_explore, attempts_today, explore_background, open_questions, query_kept, read_explore,
    research_once, research_trigger,
)
from mind.core.routing.home_marks import HomeMarks
from mind.core.config import load_thresholds
from mind.core.feeling.appraisal import parse_activity_appraisal
from mind.core.feeling.feelings import Feelings
from mind.core.lifelog import FeelingLog
from mind.core.memory.explore_sleep import prepare_pages, write_explore_page
from mind.core.memory.index import build_index
from mind.core.memory.page import load_pages, write_page
from mind.core.idea import Idea
from mind.skills.tavily_search.skill import TavilySearchSkill

NOW = datetime(2026, 10, 12, 14, tzinfo=timezone(timedelta(hours=9)))


def test_gate_never_sends_local_home_unknown_or_failed_judgement(tmp_path):
    marks = HomeMarks(tmp_path)
    assert query_kept("私だけの秘密。", "local", marks, None) == "unmarked"
    assert query_kept("私だけの秘密。", "local", marks, lambda unknown: [1]) == "home"
    assert query_kept("未判定の話。", "local", marks, lambda unknown: 1 / 0) == "judgement_failed"
    assert query_kept("既に外で書いた問い", "cloud", marks, lambda unknown: 1 / 0) is None


def test_search_attempts_are_persisted_before_search_and_fail_closed(tmp_path):
    path = tmp_path / "explore"
    called = []
    skill = TavilySearchSkill(call_fn=lambda query: called.append(query) or {
        "results": [{"title": "星", "url": "https://example.test", "content": "宇宙" * 300}]})
    kept = research_once(path, at=NOW, question="私だけの秘密。", brain="Gemma", brain_location="local",
                         shown={}, follows=None, thread=None, marks=None, judge=None, skill=skill,
                         read=lambda q, rs: {"thought": "秘密", "next": "続き"})
    assert kept["kept"] == "unmarked" and not called
    assert attempts_today(read_explore(path), NOW) == 0
    done = research_once(path, at=NOW + timedelta(seconds=1), question="星の色を知りたい", brain="Gemini",
                         brain_location="cloud", shown={"interests": ["星"]}, follows=None, thread=None,
                         marks=None, judge=None, skill=skill,
                         read=lambda q, rs: {"thought": "楽しかった", "next": "星の温度は？"})
    assert done["answer"]["thought"] == "楽しかった"
    assert called == ["星の色を知りたい"]
    lines = read_explore(path)
    assert attempts_today(lines, NOW) == 1
    assert len([l for l in lines if l.value["kind"] == "explored"]) == 1
    assert len([l for l in lines if l.value["kind"] == "explored"][0].value["results"][0]["excerpt"]) == 400
    assert open_questions(lines)[0]["question"] == "星の温度は？"


def test_research_marks_only_cloud_words_and_outside_results(tmp_path):
    marks = HomeMarks(tmp_path / "marks")
    skill = TavilySearchSkill(call_fn=lambda question: {"results": [
        {"title": "海の光", "url": "https://example.test", "content": "波長の話"},
    ]})
    research_once(tmp_path / "explore", at=NOW, question="海を調べる", brain="Gemini",
                  brain_location="cloud", shown={}, follows=None, thread=None,
                  marks=marks, judge=None, skill=skill,
                  read=lambda question, results: {"thought": "青い光", "next": "次は深さ"})
    assert not marks.home("海の光")
    assert not marks.home("波長の話")
    assert not marks.home("海を調べる")
    assert not marks.home("青い光")
    assert not marks.home("次は深さ")

    local = HomeMarks(tmp_path / "local")
    research_once(tmp_path / "local-explore", at=NOW, question="海を調べる", brain="Gemma",
                  brain_location="local", shown={}, follows=None, thread=None,
                  marks=local, judge=lambda parts: [], skill=skill,
                  read=lambda question, results: {"thought": "内緒の感想", "next": "内緒の続き"})
    assert not local.home("海の光")
    assert local.home("内緒の感想")
    assert local.home("内緒の続き")


def test_research_pack_never_shows_local_unmarked_text_to_cloud(tmp_path):
    directory = tmp_path / "explore"
    t = NOW.isoformat()
    append_explore(directory, {"kind": "explore-time", "ts": t, "thread": t,
                               "question": "秘密の問い", "kept": None})
    append_explore(directory, {"kind": "explored", "time": t, "thought": "秘密の感想",
                               "next": "秘密の続き"})
    lines = read_explore(directory)
    assert "秘密の感想" in explore_background(lines, NOW)
    marks = HomeMarks(tmp_path / "marks")
    assert explore_background(lines, NOW, marks=marks, cloud=True) == ""
    marks.mark(marks.unmarked(["秘密の問い", "秘密の感想"]), [])
    projected = explore_background(lines, NOW, marks=marks, cloud=True)
    assert "秘密の問い" in projected and "秘密の感想" in projected
    assert "秘密の続き" not in projected


def test_failed_or_unfinished_search_counts_toward_limit_without_retry(tmp_path):
    path = tmp_path / "explore"
    for i in range(6):
        append_explore(path, {"kind": "explore-time", "ts": (NOW + timedelta(seconds=i)).isoformat(),
                              "question": "問い", "kept": None, "thread": str(i)})
    skill = TavilySearchSkill(call_fn=lambda q: (_ for _ in ()).throw(AssertionError("MUST NOT SEARCH")))
    assert research_once(path, at=NOW + timedelta(minutes=1), question="別の問い", brain="Gemini",
                         brain_location="cloud", shown=None, follows=None, thread=None, marks=None,
                         judge=None, skill=skill, read=lambda q, rs: {}) is None
    assert attempts_today(read_explore(path), NOW) == 6
    assert research_trigger(read_explore(path), activity="調べものをする",
                            at=NOW + timedelta(minutes=31), selected_at=NOW - timedelta(minutes=2)) is None


def test_continuation_is_only_after_30_minutes_and_selected_activity(tmp_path):
    path = tmp_path / "explore"
    stamp = NOW.isoformat()
    append_explore(path, {"kind": "explore-time", "ts": stamp, "thread": stamp,
                          "follows": None, "kept": None, "question": "海流はなぜ動く"})
    append_explore(path, {"kind": "explored", "time": stamp, "thought": "なるほど", "next": "深層は？", "brain": "Gemma"})
    lines = read_explore(path)
    for minutes in (5, 29):
        assert research_trigger(lines, activity="調べものをする", at=NOW + timedelta(minutes=minutes),
                                selected_at=NOW - timedelta(minutes=5)) is None
    assert research_trigger(lines, activity="手紙を書く", at=NOW + timedelta(minutes=35),
                            selected_at=NOW - timedelta(minutes=5)) is None
    follow = research_trigger(lines, activity="調べものをする", at=NOW + timedelta(minutes=30),
                              selected_at=NOW - timedelta(minutes=5))
    assert follow["kind"] == "follow" and follow["question"] == "深層は？"
    assert research_trigger(lines, activity="調べものをする", at=NOW + timedelta(minutes=35),
                            selected_at=NOW - timedelta(minutes=5),
                            interrupted_at=NOW + timedelta(minutes=4)) is None
    append_explore(path, {"kind": "explore-time", "ts": (NOW + timedelta(minutes=31)).isoformat(),
                          "thread": stamp, "follows": stamp, "kept": "home", "question": "深層は？"})
    assert open_questions(read_explore(path)) == []


def test_research_episode_is_traceable_to_log_lines_and_activity_feeling(tmp_path):
    idea = SimpleNamespace(root=tmp_path, lifelog=tmp_path / "lifelog", memory=tmp_path / "memory")
    memory = SimpleNamespace(idea=idea)
    stamp = NOW.isoformat()
    first = append_explore(idea.lifelog / "explore", {
        "kind": "explore-time", "ts": stamp, "brain": "Gemini", "thread": stamp,
        "question": "海の色はなぜ青い？", "follows": None, "shown": {}, "kept": None,
    })
    second = append_explore(idea.lifelog / "explore", {
        "kind": "explored", "time": stamp, "brain": "Gemini", "failed": None,
        "thought": "青い光が残るんだ", "next": "夜の海は？",
        "results": [{"title": "光の散乱", "url": "https://example.test", "excerpt": "波長による違い"}],
    })
    feelings = Feelings(FeelingLog(idea.lifelog / "feeling"), load_thresholds().feeling)
    feelings.feel_activity(parse_activity_appraisal({"feeling": "面白かった", "valence": "とてもうれしい",
                                                     "arousal": "大きく動いた"}),
                           source=second, at=NOW + timedelta(seconds=2))
    before = NOW + timedelta(days=1)
    assert prepare_pages(memory, before=before, signed="Gemma", feelings=feelings) == 1
    assert prepare_pages(memory, before=before, signed="Gemma", feelings=feelings) == 0
    page = load_pages(idea.memory)[0]
    assert page.source == (first, second)
    assert page.affect is not None and page.affect["valence"] > 0
    written = write_explore_page(idea, page, persona="わたし", signed="Gemma",
                                 ask=lambda prompt, schema, attempt: {
                                     "title": "海の色", "gist": "青い理由を知った", "story": "自分で海を調べて面白かった。",
                                     "importance": 7, "concepts": ["海", "光の散乱"],
                                 })
    assert written.written and written.concepts == ("調べもの", "海", "光の散乱")
    assert written.body == "自分で海を調べて面白かった。"


def test_research_records_and_evidence_survive_rebuilding_local_index(tmp_path):
    idea = Idea(tmp_path)
    stamp = (NOW - timedelta(days=1)).isoformat()
    first = append_explore(idea.lifelog / "explore", {
        "kind": "explore-time", "ts": stamp, "brain": "Gemini", "thread": stamp,
        "question": "潮の動きは？", "follows": None, "shown": {}, "kept": None,
    })
    second = append_explore(idea.lifelog / "explore", {
        "kind": "explored", "time": stamp, "brain": "Gemini", "failed": None,
        "thought": "満ち引きに驚いた", "next": "月との関係は？",
        "results": [{"title": "潮の知識", "url": "https://example.org/tide", "excerpt": "潮汐は重力による"}],
    })
    memory = SimpleNamespace(idea=idea)
    assert prepare_pages(memory, before=NOW, signed="Gemma") == 1
    page = load_pages(idea.memory)[0]
    write_page(idea.memory, page.with_words(title="潮のこと", gist="海の潮を知った",
                                             importance=7, written_by="Gemma", body="潮を調べた"))
    for _ in range(2):
        build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                    embed=lambda text: [0.1, 0.2], model="test", progress=lambda text: None)
        with closing(sqlite3.connect(idea.memory_index)) as db:
            assert db.execute("SELECT count(*) FROM record_fts WHERE ref=?", (second,)).fetchone()[0] == 1
            assert {row[0] for row in db.execute("SELECT ref FROM page_sources WHERE page_id=?", (page.id,))} == {first, second}
            evidence = db.execute("SELECT evidence FROM pages WHERE id=?", (page.id,)).fetchone()[0]
            assert "潮の動きは？" in evidence and "潮汐は重力による" in evidence
