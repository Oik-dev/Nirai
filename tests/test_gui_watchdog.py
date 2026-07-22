"""app/gui_server.py の見回りスレッド本体(_watchdog_tick)のテスト。設計書 §2.4。

Serina 日界によるセッション切替と、旧 idle timeout / アイドル内職の廃止を検査する。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.app import gui_server
from serina.app.idle_config import AppTimingConfig
from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryStore
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.state.routing_rules import RoutingRules
from serina.core.state.session import SessionState

JST = ZoneInfo("Asia/Tokyo")
NOW = datetime(2026, 7, 22, 8, 0, 0, tzinfo=JST).astimezone(timezone.utc)


class StubCore:
    """end_session呼び出し回数を記録するだけのスタブ。turn_routed等は使わない。"""

    def __init__(self, chore_box: ChoreBox, memory_store: MemoryStore, thresholds: ThresholdsConfig) -> None:
        self.chore_box = chore_box
        self.memory_store = memory_store
        self.thresholds = thresholds
        self.routing_rules = RoutingRules()
        self.session = SessionState()
        self.end_session_calls = 0
        self.emotion = _StubEmotion()
        self.quota_ledger = QuotaLedger()

    def end_session(self) -> list[int]:
        self.end_session_calls += 1
        self.session = SessionState()
        return []


class _StubEmotion:
    mood_trajectory: list = []

    def summarize_trajectory(self) -> str:
        return ""

    def clear_trajectory(self) -> None:
        pass


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])


def _fresh_store() -> MemoryStore:
    return MemoryStore(str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4)


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(fusen_confidence={"default": 0.5, "記憶候補": 0.6}, mood_guard_max_delta_per_turn=0.1)


def _timing(**overrides) -> AppTimingConfig:
    base = dict(
        idle_timeout_after_seconds=300,
        idle_digest_gap_seconds=10,
        idle_poll_interval_seconds=20,
        idle_digest_chunk_limit=1,
        export_life_min_interval_seconds=999_999,
        gpu_busy_threshold_percent=40.0,
        serina_day_boundary_hour=7,
        serina_day_grace_after_activity_seconds=900,
    )
    base.update(overrides)
    return AppTimingConfig(**base)


def _make_state(
    core: StubCore,
    *,
    last_activity_at: datetime,
    last_boundary_serina_day: date | None = date(2026, 7, 21),
    session_ended: bool = False,
) -> gui_server.GuiState:
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    state.session_store = None
    state.session_mgr = MagicMock()
    state.session_mgr.rotate.return_value = "s_new"
    state.session_id = "s_test"
    state.turn_lock = threading.Lock()
    state.lane_call_fns = {"local": _stub_call_fn}
    tmp = Path(tempfile.mkdtemp())
    state.change_log = ChangeLog(tmp / "changes.jsonl")
    from serina.core.memory.protection import GenerationStore

    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.db_path = None
    state.life_dir = None
    state.summaries_path = None
    state.last_export_life_at = None
    state.last_persona_propose_at = None
    state.persona_propose_state_path = tmp / "persona_propose_state.json"
    state.last_activity_at = last_activity_at
    state.session_ended = session_ended
    state.watchdog_lock = threading.Lock()
    state.last_diary_at = last_activity_at
    state.last_diary_empty_at = None
    state.diary_state_path = tmp / "diary_state.json"
    state.serina_boundary_state_path = tmp / "serina_boundary_state.json"
    state.last_boundary_serina_day = last_boundary_serina_day
    state.pulse_state_path = tmp / "pulse.json"
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    return state


def _stub_call_fn(prompt: str) -> str:
    return json.dumps({"candidates": []})


def test_tick_does_not_end_session_on_idle_timeout(monkeypatch) -> None:  # noqa: ANN001
    """無操作タイムアウトでは end_session + rotate しない（Serina 日界のみ）。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=10_000))
    timing = _timing()

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: True)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 0
    state.session_mgr.rotate.assert_not_called()


def test_tick_does_not_digest_on_idle(monkeypatch) -> None:  # noqa: ANN001
    """セッション終了後でもアイドル内職（蒸留等）は走らない。"""
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 22),
        session_ended=True,
    )
    timing = _timing()

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert len(box.pending(kind="蒸留")) == 1


def test_tick_runs_serina_day_boundary_when_conditions_met(monkeypatch) -> None:  # noqa: ANN001
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing()
    growth_calls: list[datetime] = []

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "run_startup_chores", lambda *a, **k: MagicMock(processed=0, failed=[], total_accepted=0))
    monkeypatch.setattr(
        gui_server,
        "_run_growth_chores_for_state",
        lambda st, tm, *, now: growth_calls.append(now),
    )
    monkeypatch.setattr(gui_server, "_run_pending_diaries_for_serina_days", lambda *a, **k: 0)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 1
    state.session_mgr.rotate.assert_called_once()
    assert state.last_boundary_serina_day == date(2026, 7, 22)
    assert state.session_id == "s_new"
    assert growth_calls == [NOW], "日界フローは成長系裏方（persona/life）を必ず経由する"


def test_tick_defers_boundary_when_grace_not_elapsed(monkeypatch) -> None:  # noqa: ANN001
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=5),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing()

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 0
    state.session_mgr.rotate.assert_not_called()


def test_tick_skips_boundary_when_turn_lock_held(monkeypatch) -> None:  # noqa: ANN001
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing()

    release = threading.Event()
    holder = threading.Thread(target=lambda: (state.turn_lock.acquire(), release.wait(2), state.turn_lock.release()))
    holder.start()
    time.sleep(0.05)

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)

    try:
        gui_server._watchdog_tick_at(state, timing, now=NOW)
        assert core.end_session_calls == 0
    finally:
        release.set()
        holder.join()
