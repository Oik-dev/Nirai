"""Research watchdog guards external calls and persists unknown attempts."""

from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace

from mind.app import server
from mind.core.chores.explore import append_explore, read_explore
from mind.core.chores.letters import append_line
from mind.core.idea import Idea

NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
TIMING = SimpleNamespace(serina_day_grace_after_activity_seconds=60)


def setup(tmp_path):
    idea = Idea(tmp_path)
    append_line(idea.lifelog / "body", {"kind": "activity", "ts": NOW.isoformat(), "value": "調べものをする"})
    wake = SimpleNamespace(at=NOW - timedelta(minutes=1))
    queries, searches = [], []

    class Skill:
        enabled = True
        def search(self, question):
            searches.append(question)
            return SimpleNamespace(results=[{"title": "潮", "url": "https://example.org/tide",
                                             "snippet": "海の潮汐"}])

    class Core:
        tavily_search = Skill()
        home_marks = None
        registry = []
        brains = {}
        def choose_explore(self, **kwargs):
            queries.append(kwargs)
            return {"question": "なぜ潮が満ちる？", "brain": "gemini", "location": "cloud",
                    "shown": {"interest": ["潮"], "neighbors": []},
                    "follows": kwargs["trigger"].get("time"),
                    "thread": kwargs["trigger"].get("thread")}
        def read_explore_results(self, **kwargs):
            return {"thought": "面白かった", "next": "月との関係は？"}
        def appraise_activity(self, **kwargs):
            return None

    core = Core()
    core.memory = SimpleNamespace(idea=idea, waking=lambda: wake)
    state = SimpleNamespace(core=core, name="Serina", busy=SimpleNamespace(busy=lambda now: False),
                            conversation=SimpleNamespace(directory=idea.conversation),
                            turn_lock=Lock(), watchdog_lock=Lock(), last_activity_at=None)
    return idea, state, wake, queries, searches


def test_research_is_persisted_and_not_repeated_on_restart(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert len(queries) == 1 and searches == ["なぜ潮が満ちる？"]
    assert [line.value["kind"] for line in read_explore(idea.lifelog / "explore")] == ["explore-time", "explored"]
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(minutes=29))
    assert len(queries) == 1
    # The 30-minute follow-up can fire once, and retains the original thread.
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(minutes=31))
    assert len(queries) == 2 and len(searches) == 2
    entries = read_explore(idea.lifelog / "explore")
    assert entries[2].value["follows"] == entries[0].value["ts"]
    assert entries[2].value["thread"] == entries[0].value["thread"]


def test_research_is_silent_while_busy_locked_in_conversation_or_not_selected(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)
    state.busy = SimpleNamespace(busy=lambda now: True)
    server._maybe_research_inner(state, TIMING, now=NOW)
    state.busy = SimpleNamespace(busy=lambda now: False)
    state.turn_lock.acquire()
    try:
        server._maybe_research_inner(state, TIMING, now=NOW)
    finally:
        state.turn_lock.release()
    state.last_activity_at = NOW
    server._maybe_research_inner(state, TIMING, now=NOW)
    state.last_activity_at = None
    append_line(idea.lifelog / "body", {"kind": "activity", "ts": NOW.isoformat(), "value": "休む"})
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert not queries and not searches


def test_research_needs_key_trigger_and_room_under_six(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)
    state.core.tavily_search.enabled = False
    server._maybe_research_inner(state, TIMING, now=NOW)
    state.core.tavily_search.enabled = True
    wake.at = None
    server._maybe_research_inner(state, TIMING, now=NOW)
    wake.at = NOW - timedelta(minutes=1)
    for i in range(6):
        append_explore(idea.lifelog / "explore", {
            "kind": "explore-time", "ts": (NOW - timedelta(minutes=i + 1)).isoformat(),
            "question": "別の問い", "thread": "t", "kept": None,
        })
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert not queries and not searches
