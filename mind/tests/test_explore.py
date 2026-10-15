from datetime import datetime, timedelta, timezone
from dataclasses import replace
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
from mind.core.memory.index import MemoryIndex, build_index
from mind.core.memory.lookup import RecordLookup, _still_present, _rows_for
from mind.core.memory.recall import Cue, Recaller
from mind.core.memory.page import load_pages, write_page
from mind.core.idea import Idea
from mind.core.runtime import Core
from mind.core.routing.registry import BrainEntry
from mind.skills.tavily_search.skill import TavilySearchSkill

NOW = datetime(2026, 10, 12, 14, tzinfo=timezone(timedelta(hours=9)))


def test_followup_reuses_exact_question_without_new_brain_call(tmp_path):
    question = "月と潮の関係をもっと知りたい。"
    marks = HomeMarks(tmp_path)
    entries = [BrainEntry("gemini", "gemini", "cloud", "large"),
               BrainEntry("gemma", "ollama", "local", "small")]

    class NoQuestionGeneration:
        enabled = True

        def ready(self, now=None):  # noqa: ANN001, ANN202, ARG002
            return True

        def raw_call(self, prompt):
            raise AssertionError("続きの問いは脳に作り直させない")

    core = Core(persona_text="", absolute_rules="", thresholds=load_thresholds(),
                memory=SimpleNamespace(index=SimpleNamespace(pages={})),
                registry=entries, brains={name: NoQuestionGeneration() for name in ("gemini", "gemma")},
                home_marks=marks)
    trigger = {"kind": "follow", "question": question, "time": NOW.isoformat(), "thread": "original"}
    # A locally written, unmarked follow-up cannot route its reading to Gemini.
    local = core.choose_explore(now=NOW, trigger=trigger)
    assert local == {"brain": "gemma", "location": "local", "question": question,
                     "follows": NOW.isoformat(), "thread": "original", "shown": None}
    # A cloud-authored (already outside) question may route to the cloud,
    # but neither choice consumes a question-generation call.
    marks.mark([question], [])
    cloud = core.choose_explore(now=NOW, trigger=trigger)
    assert cloud["brain"] == "gemini" and cloud["question"] == question


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
    kept = research_once(path, at=NOW, question="私だけの秘密。", brain="Gemma", question_location="local", reading_location="local",
                         shown={}, follows=None, thread=None, marks=None, judge=None, skill=skill,
                         read=lambda q, rs: {"thought": "秘密", "next": "続き"})
    assert kept["kept"] == "unmarked" and not called
    assert attempts_today(read_explore(path), NOW) == 0
    done = research_once(path, at=NOW + timedelta(seconds=1), question="星の色を知りたい", brain="Gemini",
                         question_location="cloud", reading_location="cloud", shown={"interests": ["星"]}, follows=None, thread=None,
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
                  question_location="cloud", reading_location="cloud", shown={}, follows=None, thread=None,
                  marks=marks, judge=None, skill=skill,
                  read=lambda question, results: {"thought": "青い光", "next": "次は深さ"})
    assert not marks.home("海の光")
    assert not marks.home("波長の話")
    assert not marks.home("海を調べる")
    assert not marks.home("青い光")
    assert not marks.home("次は深さ")

    local = HomeMarks(tmp_path / "local")
    research_once(tmp_path / "local-explore", at=NOW, question="海を調べる", brain="Gemma",
                  question_location="local", reading_location="local", shown={}, follows=None, thread=None,
                  marks=local, judge=lambda parts: [], skill=skill,
                  read=lambda question, results: {"thought": "内緒の感想", "next": "内緒の続き"})
    assert not local.home("海の光")
    assert local.home("内緒の感想")
    assert local.home("内緒の続き")


def test_followup_question_author_and_reading_brain_have_separate_provenance(tmp_path):
    skill = TavilySearchSkill(call_fn=lambda question: {"results": [
        {"title": "潮", "url": "https://example.test/tides", "content": "月と潮"},
    ]})
    for origin, reader in (("cloud", "local"), ("local", "cloud")):
        root = tmp_path / f"{origin}-{reader}"
        marks = HomeMarks(root / "marks")
        question = f"{origin} が書いた問い"
        thought = f"{reader} が読んだ感想"
        nxt = f"{reader} が書いた続き"
        result = research_once(root / "explore", at=NOW, question=question, brain=reader,
                               question_location=origin, reading_location=reader,
                               shown=None, follows="before", thread="a", marks=marks,
                               judge=lambda parts: [], skill=skill,
                               read=lambda q, rows: {"thought": thought, "next": nxt}, followup=True)
        assert result["answer"]["thought"] == thought
        assert marks.home(thought) == (reader == "local")
        assert marks.home(nxt) == (reader == "local")
        assert not marks.home(question)
        projected = explore_background(read_explore(root / "explore"), NOW, marks=marks, cloud=True)
        assert (thought in projected) == (reader == "cloud")
        assert (nxt in projected) == (reader == "cloud")


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
                         question_location="cloud", reading_location="cloud", shown=None, follows=None, thread=None, marks=None,
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


def test_research_source_verified_for_lookup_and_not_returned_after_deletion(tmp_path):
    idea = Idea(tmp_path)
    stamp = (NOW - timedelta(days=1)).isoformat()
    append_explore(idea.lifelog / "explore", {"kind": "explore-time", "ts": stamp,
                  "thread": stamp, "question": "Tidalforces cause tides?", "kept": None})
    source = append_explore(idea.lifelog / "explore", {"kind": "explored", "time": stamp,
             "failed": None, "results": [{"title": "Tidalforces", "url": "https://example.test",
              "excerpt": "Gravity causes tides"}], "thought": "Tidalforces are fascinating", "next": ""})
    assert prepare_pages(SimpleNamespace(idea=idea), before=NOW, signed="fake") == 1
    page = replace(load_pages(idea.memory)[0], concepts=("Tidalforces",))
    write_page(idea.memory, page.with_words(title="Tidalforces", gist="Tidalforces cause tides",
                                             importance=5, written_by="fake", body="Tidalforces are interesting"))
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=lambda _: [1.0, 0.0], model="fake", progress=lambda _: None)
    recaller = Recaller(MemoryIndex.load(idea.memory_index), embed=lambda _: [1.0, 0.0])
    lookup = RecordLookup(idea.memory_index, idea.conversation)
    cue = Cue("Tidalforces", (), NOW)
    with closing(sqlite3.connect(idea.memory_index)) as db:
        candidates = _rows_for(db, "tidalforces")
    assert len(candidates) == 1 and _still_present(idea.conversation, candidates[0])
    assert any(record.ref == source for record in lookup.search(cue, recaller, limit=8)[0])
    assert any(record.ref == source for record in lookup.evidence(page.id, cue, recaller, limit=8))
    (tmp_path / source.split("#")[0]).unlink()
    assert not _still_present(idea.conversation, candidates[0])
    assert lookup.search(cue, recaller, limit=8)[0] == []
    assert lookup.evidence(page.id, cue, recaller, limit=8) == []
