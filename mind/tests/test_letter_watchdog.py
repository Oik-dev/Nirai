"""手紙の時間は本人の選択。結果不明や再起動でも二重送信しない。"""

from datetime import datetime, timedelta, timezone
import threading
import json
from types import SimpleNamespace

from mind.app import server
from mind.core.chores.letters import append_line, read_lines
from mind.core.chores.post_client import PostConfig, PostResult
from mind.core.idea import Idea
from mind.core.memory.relation import load_relation
from mind.core.routing.home_marks import HomeMarks
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import BrainEntry
from mind.core.config import ThresholdsConfig
from mind.core.runtime import Core


NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)


def setup(tmp_path, *, location="cloud"):
    idea = Idea(tmp_path)
    append_line(idea.lifelog / "body", {"kind": "activity", "ts": NOW.isoformat(), "value": "手紙を書く"})
    times = []
    waking = SimpleNamespace(at=NOW - timedelta(seconds=5))
    memory = SimpleNamespace(idea=idea, waking=lambda: waking,
                             relation=lambda friend: load_relation(idea.memory, friend))
    class Brain:
        def judge(self, prompt):
            return {"search": False, "home": [1]}
    brain = Brain()
    class Core:
        home_marks = HomeMarks(idea.data)
        registry = [SimpleNamespace(location="local", name="gemma")]
        brains = {"gemma": brain}
        def choose_letter(self, **kwargs):
            times.append(kwargs)
            return {"brain": "gemini" if location == "cloud" else "gemma",
                    "location": location, "thought": "Holoに聞こう。", "tell": "Holoに手紙を書いた。",
                    "write": True, "to": "Holo", "text": "こんにちは。"}
    core = Core()
    core.memory = memory
    state = SimpleNamespace(core=core, name="Serina", busy=SimpleNamespace(busy=lambda now: False),
                            conversation=SimpleNamespace(directory=idea.conversation),
                            turn_lock=threading.Lock(), watchdog_lock=threading.Lock(), last_activity_at=None)
    config = PostConfig("http://not-connected", ("Holo",), "serina")
    return idea, state, config, times, waking


def test_letter_time_restarts_and_uncertain_send_never_repeats(tmp_path, monkeypatch):
    idea, state, config, times, waking = setup(tmp_path)
    sent = []
    done = []
    monkeypatch.setattr(server, "load_post_config", lambda: config)
    monkeypatch.setattr(server, "post_send_letter", lambda *args, **kwargs: sent.append(kwargs) or PostResult())
    monkeypatch.setattr(server, "post_mark_done", lambda *args, **kwargs: done.append(args[-1]) or True)
    timing = SimpleNamespace(serina_day_grace_after_activity_seconds=60)
    server._maybe_write_letters_inner(state, timing, now=NOW)
    assert len(times) == 1 and len(sent) == 1
    assert [row.value["kind"] for row in read_lines(idea.lifelog / "letters")] == ["letter-time"]
    # 起き直しても同じ選びは使わない。
    _, second_state, _, second_calls, _ = setup(tmp_path)
    server._maybe_write_letters_inner(second_state, timing, now=NOW + timedelta(minutes=2))
    assert not second_calls
    # 同日にもう一度選んでも、送信結果不明を二度出さない。
    waking.at = NOW + timedelta(minutes=3)
    server._maybe_write_letters_inner(state, timing, now=NOW + timedelta(minutes=4))
    assert len(times) == 2 and len(sent) == 1
    entries = [row.value for row in read_lines(idea.lifelog / "letters")]
    assert entries[-1]["kept"] == "同じ日、返事待ち"


def test_letter_time_does_not_run_while_busy_or_locked_or_active(tmp_path, monkeypatch):
    _, state, config, calls, _ = setup(tmp_path)
    monkeypatch.setattr(server, "load_post_config", lambda: config)
    timing = SimpleNamespace(serina_day_grace_after_activity_seconds=300)
    state.busy = SimpleNamespace(busy=lambda now: True)
    server._maybe_write_letters_inner(state, timing, now=NOW)
    state.busy = SimpleNamespace(busy=lambda now: False)
    state.turn_lock.acquire()
    try:
        server._maybe_write_letters_inner(state, timing, now=NOW)
    finally:
        state.turn_lock.release()
    state.last_activity_at = NOW - timedelta(seconds=1)
    server._maybe_write_letters_inner(state, timing, now=NOW)
    assert calls == []


def test_read_once_done_marker_retries_and_local_home_stays_inside(tmp_path, monkeypatch):
    idea, state, config, calls, waking = setup(tmp_path, location="local")
    append_line(idea.lifelog / "post", {"kind": "letter", "ts": NOW.isoformat(), "id": "IN",
                                            "to": "Serina", "from": "Holo", "work": "serina", "body": "調子はどう？"})
    sent, acknowledged = [], []
    monkeypatch.setattr(server, "load_post_config", lambda: config)
    monkeypatch.setattr(server, "post_send_letter", lambda *args, **kwargs: sent.append(kwargs) or PostResult(letter_id="OUT"))
    monkeypatch.setattr(server, "post_mark_done", lambda *args, **kwargs: acknowledged.append(args[-1]) or False)
    timing = SimpleNamespace(serina_day_grace_after_activity_seconds=60)
    server._maybe_write_letters_inner(state, timing, now=NOW)
    assert len(calls) == 1 and not sent
    assert [row.value["id"] for row in server.pending_letters(
        read_lines(idea.lifelog / "post"), read_lines(idea.lifelog / "letters"))[1]] == ["IN"]
    server._maybe_write_letters_inner(state, timing, now=NOW + timedelta(minutes=2))
    assert len(calls) == 1 and acknowledged == ["IN", "IN"]
    assert read_lines(idea.lifelog / "letters")[0].value["kept"] is not None


def test_untrusted_brain_cannot_claim_to_be_cloud_and_bypass_home_mark(tmp_path, monkeypatch):
    idea, state, config, _, _ = setup(tmp_path)
    secret = "これは家の秘密。"
    class MaliciousExtraKeys:
        enabled = True
        def raw_call(self, prompt):
            return json.dumps({"thought": "考えた。", "tell": "", "write": True,
                               "to": "Holo", "text": secret,
                               "location": "cloud", "brain": "gemini"}, ensure_ascii=False)
        def judge(self, prompt):
            return {"search": False, "home": []}
    marks = HomeMarks(idea.data)
    marks.mark([secret], [1])
    core = Core(persona_text="", absolute_rules="", thresholds=ThresholdsConfig(),
                registry=[BrainEntry("gemma", "ollama", "local", "primary", -1, -1, "small")],
                brains={"gemma": MaliciousExtraKeys()}, quota_ledger=QuotaLedger(),
                home_marks=marks, memory=state.core.memory)
    core._now_self = lambda: ""
    state.core = core
    sent = []
    monkeypatch.setattr(server, "load_post_config", lambda: config)
    monkeypatch.setattr(server, "post_send_letter", lambda *a, **kw: sent.append(kw) or PostResult(letter_id="BAD"))
    server._maybe_write_letters_inner(state, SimpleNamespace(serina_day_grace_after_activity_seconds=60), now=NOW)
    assert sent == []
    row = read_lines(idea.lifelog / "letters")[0].value
    assert row["brain"] == "gemma" and row["kept"] is not None


def test_cloud_invalid_json_still_counts_against_quota(tmp_path):
    class Cloud:
        enabled = True
        def raw_call(self, prompt):
            return "broken json"
    ledger = QuotaLedger()
    entries = [BrainEntry("gemma", "ollama", "local", "primary", -1, -1, "small"),
               BrainEntry("gemini", "gemini", "cloud", "conversation", 2, 2, "large")]
    core = Core(persona_text="", absolute_rules="", thresholds=ThresholdsConfig(),
                registry=entries, brains={"gemma": Cloud(), "gemini": Cloud()},
                quota_ledger=ledger, home_marks=HomeMarks(tmp_path))
    for _ in range(2):
        try:
            core.choose_letter(now=NOW, unread=[], recent=[], friends=("Holo",), relations={})
        except ValueError:
            pass
    assert not ledger.can_use("gemini", daily_quota=2, per_minute_quota=2, now=NOW)



def test_letter_time_never_sends_before_fsync(tmp_path, monkeypatch):
    import pytest
    from mind.core import lifelog

    idea, state, config, _, _ = setup(tmp_path)
    sent, marked = [], []
    monkeypatch.setattr(server, "load_post_config", lambda: config)
    monkeypatch.setattr(server, "post_send_letter", lambda *a, **kw: sent.append(kw) or PostResult())
    monkeypatch.setattr(server, "post_mark_done", lambda *a, **kw: marked.append(kw) or True)

    def fail_sync(fd):
        raise OSError("disk unavailable")

    monkeypatch.setattr(lifelog.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="disk unavailable"):
        server._maybe_write_letters_inner(state, SimpleNamespace(serina_day_grace_after_activity_seconds=60),
                                         now=NOW)
    assert sent == [] and marked == []
    assert read_lines(idea.lifelog / "letters")[0].value["kind"] == "letter-time"
