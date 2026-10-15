"""Research watchdog guards external calls and persists unknown attempts."""

from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace

from mind.app import server
from mind.core.chores.explore import append_explore, read_explore
from mind.core.chores.letters import append_line
from mind.core.idea import Idea
from mind.core.routing.home_marks import HomeMarks

NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)
TIMING = SimpleNamespace(serina_day_grace_after_activity_seconds=60,
                         research_agent="antigravity-preview-09-2026",
                         research_agent_model="gemini-3.5-flash-lite",
                         research_agent_max_tokens=100000)


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
        registry = [SimpleNamespace(name="gemini", location="cloud")]
        brains = {}
        def choose_explore(self, **kwargs):
            queries.append(kwargs)
            trigger = kwargs["trigger"]
            return {"question": trigger["question"] if trigger["kind"] == "follow" else "なぜ潮が満ちる？",
                    "brain": "gemini", "location": "cloud",
                    "shown": {"interest": ["潮"], "neighbors": []},
                    "follows": trigger.get("time"),
                    "thread": trigger.get("thread")}
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


def test_agent_waits_without_holding_turn_lock_and_records_once(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)

    class Agent:
        enabled = True
        started = []
        polls = []
        def ready(self, now=None):
            return True
        def start(self, question, **kwargs):
            self.started.append((question, kwargs))
            return 'agent-123'
        def get(self, identifier):
            self.polls.append(identifier)
            if len(self.polls) == 1:
                return {'status': 'in_progress'}
            return {'status': 'completed', 'steps': [
                {'type': 'user_input', 'content': [
                    {'type': 'text', 'text': '[{"title":"入力の見本","url":"https://decoy.test"}]'}]},
                {'type': 'tool_result', 'content': [
                    {'type': 'text', 'text': '[{"title":"道具の途中","url":"https://decoy.test"}]'}]},
                {'type': 'model_output', 'content': [
                    {'type': 'text', 'text': '[{"title":"潮","url":"https://example.org/tide","excerpt":"海の潮汐"}]'}]},
            ]}
    agent = Agent()
    state.core.research_agent = agent
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert not state.turn_lock.locked() and not searches
    assert [line.value['kind'] for line in read_explore(idea.lifelog / 'explore')] == [
        'explore-time', 'explore-agent']
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(seconds=1))
    assert len(agent.polls) == 1
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(seconds=30))
    assert len(agent.polls) == 1
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(seconds=61))
    assert len(agent.polls) == 2
    rows = [line.value for line in read_explore(idea.lifelog / 'explore')]
    assert rows[-1]['kind'] == 'explored' and rows[-1]['source'] == 'antigravity'
    assert rows[-1]['thought'] == '面白かった' and not searches
    assert [item['url'] for item in rows[-1]['results']] == ['https://example.org/tide']


def test_agent_timeout_cancels_without_tavily_retry(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)
    class Agent:
        enabled = True
        cancelled = []
        def ready(self, now=None): return True
        def start(self, question, **kwargs): return 'agent-123'
        def get(self, identifier): return {'status': 'in_progress'}
        def cancel(self, identifier): self.cancelled.append(identifier)
    agent = Agent()
    state.core.research_agent = agent
    server._maybe_research_inner(state, TIMING, now=NOW)
    server._maybe_research_inner(state, TIMING, now=NOW + timedelta(minutes=21))
    assert agent.cancelled == ['agent-123']
    assert read_explore(idea.lifelog / 'explore')[-1].value['failed']
    assert not searches


def test_agent_cannot_start_falls_back_to_tavily(tmp_path):
    idea, state, wake, queries, searches = setup(tmp_path)
    class Agent:
        enabled = True
        def ready(self, now=None): return True
        def start(self, question, **kwargs): raise ConnectionError('offline')
    state.core.research_agent = Agent()
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert searches == ['なぜ潮が満ちる？']
    assert read_explore(idea.lifelog / 'explore')[-1].value['source'] == 'tavily'


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
    assert searches[1] == "月との関係は？"
    entries = read_explore(idea.lifelog / "explore")
    assert entries[2].value["follows"] == entries[0].value["ts"]
    assert entries[2].value["thread"] == entries[0].value["thread"]
    assert entries[2].value["brain"] is None


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


def test_locally_authored_followup_is_gated_even_if_reader_is_cloud(tmp_path, monkeypatch):
    idea, state, wake, queries, searches = setup(tmp_path)
    wake.at = NOW - timedelta(hours=1)
    state.core.home_marks = HomeMarks(idea.data)
    state.core.registry.append(SimpleNamespace(name="gemma", location="local"))
    state.core.brains = {"gemma": object()}
    from mind.core.routing import tavily_rules
    monkeypatch.setattr(tavily_rules, "decide_tavily_search", lambda *args, **kwargs:
                        SimpleNamespace(home_valid=True, home_indices=(1,)))
    old = (NOW - timedelta(minutes=31)).isoformat()
    append_explore(idea.lifelog / "explore", {
        "kind": "explore-time", "ts": old, "brain": "gemma", "thread": old,
        "question": "前の問い", "follows": None, "kept": None,
    })
    append_explore(idea.lifelog / "explore", {
        "kind": "explored", "time": old, "brain": "gemma", "thought": "前の感想",
        "next": "家に置く続き。", "results": [], "failed": None,
    })
    server._maybe_research_inner(state, TIMING, now=NOW)
    assert len(queries) == 1 and searches == []
    last = read_explore(idea.lifelog / "explore")[-1].value
    assert last["kind"] == "explore-time" and last["brain"] is None
    assert last["kept"] == "home" and last["question"] == "家に置く続き。"
