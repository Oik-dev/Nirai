from datetime import datetime, timedelta, timezone

from mind.core.chores.explore import (
    append_explore, attempts_today, open_questions, query_kept, read_explore,
    research_once, research_trigger,
)
from mind.core.routing.home_marks import HomeMarks
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
    append_explore(path, {"kind": "explore-time", "ts": (NOW + timedelta(minutes=31)).isoformat(),
                          "thread": stamp, "follows": stamp, "kept": "home", "question": "深層は？"})
    assert open_questions(read_explore(path)) == []
