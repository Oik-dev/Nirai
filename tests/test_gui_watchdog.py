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
    state.last_episodic_at = last_activity_at
    state.last_diary_empty_at = None
    state.episodic_state_path = tmp / "episodic_state.json"
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


def test_tick_boundary_survives_growth_chore_failure(monkeypatch) -> None:  # noqa: ANN001
    """2026-07-23是正: 成長系裏方が例外を吐いても、日記・セッション切替・日界
    マーキングは実行され、次tickで無限リトライしないこと（実機で発生したバグの回帰防止）。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing()
    growth_calls: list[datetime] = []
    diary_calls: list[datetime] = []

    def _boom(st, tm, *, now):
        growth_calls.append(now)
        raise RuntimeError("persona propose 保存失敗を模した例外")

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "run_startup_chores", lambda *a, **k: MagicMock(processed=0, failed=[], total_accepted=0))
    monkeypatch.setattr(gui_server, "_run_growth_chores_for_state", _boom)
    monkeypatch.setattr(
        gui_server,
        "_run_pending_diaries_for_serina_days",
        lambda *a, **k: (diary_calls.append(k.get("now")), 0)[1],
    )

    # 1回目: 成長系裏方が例外を吐いても、後続（日記・セッション切替・マーキング）は進む
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert growth_calls == [NOW]
    assert diary_calls == [NOW], "成長系裏方が失敗しても日記生成フェーズは実行されること"
    assert core.end_session_calls == 1
    state.session_mgr.rotate.assert_called_once()
    assert state.last_boundary_serina_day == date(2026, 7, 22), (
        "失敗しても日界マーキングは進む（さもないと次tickで無限リトライになる）"
    )

    # 2回目: 同じ now でもう一度tickしても、マーキング済みなので日界本体に再突入しない
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert growth_calls == [NOW], "マーキング済みなら2回目のtickは日界処理を再実行しない"
    assert core.end_session_calls == 1


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


def test_run_pending_diaries_splits_by_serina_day() -> None:
    """2026-07-25是正: 長期間未起動後、複数日分の未処理期間を1本に一括圧縮する事故を防ぐ。

    Serina日ごとに1本、その日の記憶だけを材料にすること（他日の記憶が混ざらないこと）を検査する。
    """
    from serina.core.state.serina_day import serina_day_start

    boundary_hour = 7
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())

    day1_start = serina_day_start(date(2026, 7, 19), boundary_hour=boundary_hour)
    day2_start = serina_day_start(date(2026, 7, 20), boundary_hour=boundary_hour)
    day3_start = serina_day_start(date(2026, 7, 21), boundary_hour=boundary_hour)

    core.memory_store.add_memory(
        "3日前の出来事", type="semantic", importance=0.5, sensitivity_grade=0,
        protection_grade="B", created_at=(day1_start + timedelta(hours=2)).isoformat(),
    )
    core.memory_store.add_memory(
        "2日前の出来事", type="semantic", importance=0.5, sensitivity_grade=0,
        protection_grade="B", created_at=(day2_start + timedelta(hours=2)).isoformat(),
    )
    core.memory_store.add_memory(
        "1日前の出来事", type="semantic", importance=0.5, sensitivity_grade=0,
        protection_grade="B", created_at=(day3_start + timedelta(hours=2)).isoformat(),
    )

    prompts: list[str] = []

    def _call(prompt: str) -> str:
        prompts.append(prompt)
        return "日記本文"

    state = _make_state(core, last_activity_at=NOW - timedelta(days=4))
    state.last_episodic_at = day1_start
    state.lane_call_fns = {"local": _call}
    timing = _timing()

    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW)

    assert generated == 3
    assert len(prompts) == 3
    assert "3日前の出来事" in prompts[0]
    assert "2日前の出来事" not in prompts[0] and "1日前の出来事" not in prompts[0]
    assert "2日前の出来事" in prompts[1]
    assert "3日前の出来事" not in prompts[1] and "1日前の出来事" not in prompts[1]
    assert "1日前の出来事" in prompts[2]
    assert "3日前の出来事" not in prompts[2] and "2日前の出来事" not in prompts[2]
    # 全期間を処理し終えたら、次のSerina日(今日)開始時刻より前で止まる
    assert state.last_episodic_at == serina_day_start(date(2026, 7, 22), boundary_hour=boundary_hour)


def test_run_pending_diaries_respects_max_count() -> None:
    """2026-07-25是正(I-4)＋2026-07-26追補: 長期未起動後の未処理日を無制限に一括生成しない。
    max_count到達で打ち切り、last_episodic_atは処理済み分までしか前進しない
    （残りは次の日界・約24時間後まで持ち越し。20秒間隔の見回りtickでは回収されない）。
    ただし「直前1日分」（気分の軌跡を消費する回）は上限に関わらず通すため、
    ここでは打ち切り境界が直前1日分の手前に来るよう4日分の未処理を用意する。"""
    from serina.core.state.serina_day import serina_day_start

    boundary_hour = 7
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())

    day1_start = serina_day_start(date(2026, 7, 18), boundary_hour=boundary_hour)
    day2_start = serina_day_start(date(2026, 7, 19), boundary_hour=boundary_hour)
    day3_start = serina_day_start(date(2026, 7, 20), boundary_hour=boundary_hour)
    day4_start = serina_day_start(date(2026, 7, 21), boundary_hour=boundary_hour)  # NOW基準の「直前1日分」

    for dt, text in [
        (day1_start, "4日前の出来事"),
        (day2_start, "3日前の出来事"),
        (day3_start, "2日前の出来事"),
        (day4_start, "1日前の出来事"),
    ]:
        core.memory_store.add_memory(
            text, type="semantic", importance=0.5, sensitivity_grade=0,
            protection_grade="B", created_at=(dt + timedelta(hours=2)).isoformat(),
        )

    state = _make_state(core, last_activity_at=NOW - timedelta(days=5))
    state.last_episodic_at = day1_start
    state.lane_call_fns = {"local": lambda p: "日記本文"}
    timing = _timing()

    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW, max_count=2)

    assert generated == 2, "直前1日分の手前で上限に到達したら打ち切る"
    assert state.last_episodic_at == day3_start, "3・4日目分は未処理のまま残り、次の日界に持ち越す"

    # I-3配線の実測: _run_pending_diaries_for_serina_days が created_at=対象日の終わり
    # （day_end）で実際に保存していること（diary.py単体テストは引数受け渡ししか
    # 検査しないため、呼び出し側の配線欠落はここでしか検出できない）。
    saved = sorted(
        (r for r in core.memory_store.list_by_type("episodic")), key=lambda r: r.id,
    )
    assert len(saved) == 2
    assert saved[0].created_at == day2_start.isoformat(), "1日目分はday_end(=2日目開始)で保存"
    assert saved[1].created_at == day3_start.isoformat(), "2日目分はday_end(=3日目開始)で保存"


def test_run_pending_diaries_always_reaches_most_recent_day_despite_max_count() -> None:
    """2026-07-26追補(serina-code-reviewer Important指摘): 直前1日分（軌跡を消費する回）は
    上限を1件超えてでも同じtickで通す。直前1日分はキュー末尾なので、これが無いと
    「上限でちょうど1件はみ出る」ケースで軌跡クリアが次の日界（約24時間後）まで
    先送りされ続け、複数の実時間日にまたがる軌跡が1本の日記に混入するリスクがある。"""
    from serina.core.state.serina_day import serina_day_start

    boundary_hour = 7
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    core.emotion = _TrackingEmotion()

    day1_start = serina_day_start(date(2026, 7, 19), boundary_hour=boundary_hour)
    day2_start = serina_day_start(date(2026, 7, 20), boundary_hour=boundary_hour)
    day3_start = serina_day_start(date(2026, 7, 21), boundary_hour=boundary_hour)  # NOW基準の直前1日分

    for dt, text in [
        (day1_start, "2日前の出来事"),
        (day2_start, "1日前の出来事の前"),
        (day3_start, "1日前の出来事"),
    ]:
        core.memory_store.add_memory(
            text, type="semantic", importance=0.5, sensitivity_grade=0,
            protection_grade="B", created_at=(dt + timedelta(hours=2)).isoformat(),
        )

    state = _make_state(core, last_activity_at=NOW - timedelta(days=4))
    state.last_episodic_at = day1_start
    state.lane_call_fns = {"local": lambda p: "日記本文"}
    timing = _timing()

    # max_count=2だが未処理は3日分。直前1日分(day3)が末尾に来るため、
    # 通常なら2件で打ち切られるところを3件目まで通す。
    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW, max_count=2)

    assert generated == 3, "直前1日分は上限を超えても同じtickで処理する"
    assert state.last_episodic_at == serina_day_start(date(2026, 7, 22), boundary_hour=boundary_hour), (
        "直前1日分まで届いたので全期間を処理し終える"
    )
    assert core.emotion.clear_calls == 1, "先送りされず同じtickで軌跡がクリアされる"


def test_tick_boundary_passes_diary_catchup_max_count(monkeypatch) -> None:  # noqa: ANN001
    """2026-07-25是正(I-4): 日界フローはtiming.diary_catchup_max_countを日記生成へ渡す。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing(diary_catchup_max_count=3)
    captured: dict = {}

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "run_startup_chores", lambda *a, **k: MagicMock(processed=0, failed=[], total_accepted=0))
    monkeypatch.setattr(gui_server, "_run_growth_chores_for_state", lambda *a, **k: None)

    def _capture(*args, **kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(gui_server, "_run_pending_diaries_for_serina_days", _capture)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert captured.get("max_count") == 3


class _TrackingEmotion:
    """気分の軌跡の使用回数・クリア回数を記録するスタブ（I-2回帰検査用）。"""

    def __init__(self, summary: str = "軌跡:今日は嬉しかった") -> None:
        self.summary = summary
        self.summarize_calls = 0
        self.clear_calls = 0
        self.mood_trajectory: list = []

    def summarize_trajectory(self) -> str:
        self.summarize_calls += 1
        return self.summary

    def clear_trajectory(self) -> None:
        self.clear_calls += 1


def test_run_pending_diaries_only_uses_mood_for_most_recent_day() -> None:
    """2026-07-25是正(I-2): 気分の軌跡は「直前1日分」の生成にだけ使い、
    2日以上前のキャッチアップ分には今日近辺の軌跡を混ぜない。"""
    from serina.core.state.serina_day import serina_day_start

    boundary_hour = 7
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    core.emotion = _TrackingEmotion()

    day1_start = serina_day_start(date(2026, 7, 19), boundary_hour=boundary_hour)
    day2_start = serina_day_start(date(2026, 7, 20), boundary_hour=boundary_hour)
    day3_start = serina_day_start(date(2026, 7, 21), boundary_hour=boundary_hour)

    for dt, text in [
        (day1_start, "3日前の出来事"),
        (day2_start, "2日前の出来事"),
        (day3_start, "1日前の出来事"),
    ]:
        core.memory_store.add_memory(
            text, type="semantic", importance=0.5, sensitivity_grade=0,
            protection_grade="B", created_at=(dt + timedelta(hours=2)).isoformat(),
        )

    prompts: list[str] = []

    def _call(prompt: str) -> str:
        prompts.append(prompt)
        return "日記本文"

    state = _make_state(core, last_activity_at=NOW - timedelta(days=4))
    state.last_episodic_at = day1_start
    state.lane_call_fns = {"local": _call}
    timing = _timing()

    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW)

    assert generated == 3
    assert "軌跡:今日は嬉しかった" not in prompts[0], "2日以上前の分には軌跡を混ぜない"
    assert "軌跡:今日は嬉しかった" not in prompts[1], "2日以上前の分には軌跡を混ぜない"
    assert "軌跡:今日は嬉しかった" in prompts[2], "直前1日分には軌跡を使う"
    assert core.emotion.clear_calls == 1, "軌跡を使った回だけクリアする"
