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

    def list_schedule_pulse_candidates(self, now, schedule_pulse_state):  # noqa: ANN001
        return []


class _StubEmotion:
    mood_trajectory: list = []
    current_day: str | None = None

    def summarize_trajectory(self, day: str | None = None) -> str:
        return ""

    def clear_trajectory(self, day: str | None = None) -> None:
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
    has_had_first_turn: bool = True,
) -> gui_server.GuiState:
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    # 2026-08-01是正: 既存テストはほぼ全て「会話が既に進行中」の状況を想定しているため
    # 既定Trueにする。「起動直後・初回ターン未到達」を検査したいテストだけ明示的にFalseを渡す。
    state.has_had_first_turn = has_had_first_turn
    state.session_store = None
    state.session_mgr = MagicMock()
    state.session_mgr.rotate.return_value = "s_new"
    state.session_id = "s_test"
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
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
    state.episodic_state_path = tmp / "episodic_state.json"
    state.serina_boundary_state_path = tmp / "serina_boundary_state.json"
    state.last_boundary_serina_day = last_boundary_serina_day
    state.pulse_state_path = tmp / "pulse.json"
    state.schedule_pulse_state_path = tmp / "schedule_pulse.json"
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


def test_tick_end_session_runs_before_distillation_consumption(monkeypatch) -> None:  # noqa: ANN001
    """2026-07-31是正(completion-review I-A): 端数flush(end_session、器に満たないターンを
    宿題箱へ積む処理)は、同じ日界内の蒸留消化(run_startup_chores)より前に実行される必要が
    ある。後だと端数が同じ日界の蒸留消化に間に合わず、次の日界にはlast_episodic_atが前進
    済みで、発話時刻を刻む記憶がどの日記材料窓にも入らず永久脱落する（本修正の要）。
    呼び出し順序そのものを固定する回帰テスト。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
    )
    timing = _timing()
    call_order: list[str] = []
    original_end_session = core.end_session

    def _tracked_end_session() -> list[int]:
        call_order.append("end_session")
        return original_end_session()

    core.end_session = _tracked_end_session

    def _tracked_startup_chores(*a, **k):  # noqa: ANN002, ANN003
        call_order.append("run_startup_chores")
        return MagicMock(processed=0, failed=[], total_accepted=0)

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "run_startup_chores", _tracked_startup_chores)
    monkeypatch.setattr(gui_server, "_run_growth_chores_for_state", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "_run_pending_diaries_for_serina_days", lambda *a, **k: 0)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert call_order == ["end_session", "run_startup_chores"]


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


def test_run_pending_diaries_does_not_advance_window_when_distillation_pending() -> None:
    """2026-07-31是正(completion-review C-1): 「材料なし」は「本当に会話が無かった日」と
    「蒸留がまだ未消化なだけ」を区別できない。宿題箱に未消化の蒸留ジョブが残っている状態で
    窓を前進させると、docs/設計書.md §4.5「材料窓は成功時のみ前進」に反し、後で蒸留が
    完了した記憶(created_at=発話時刻)がどの日記材料窓にも入らず永久に脱落する
    （created_atを発話時刻で刻むようにした本修正の副作用への対応）。"""
    from serina.core.state.serina_day import serina_day_start

    boundary_hour = 7
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    core.chore_box.enqueue(
        "蒸留", lane="local",
        payload={"turns": [{"speaker": "master", "text": "未消化の会話", "ts": None}]},
    )

    day1_start = serina_day_start(date(2026, 7, 19), boundary_hour=boundary_hour)
    state = _make_state(core, last_activity_at=NOW - timedelta(days=2))
    state.last_episodic_at = day1_start
    state.lane_call_fns = {"local": lambda prompt: "日記本文"}
    timing = _timing()

    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW)

    assert generated == 0
    # 未消化の蒸留ジョブが残っている間は、材料が無くても窓を前進させない
    assert state.last_episodic_at == day1_start


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


def test_run_pending_diaries_max_count_is_a_strict_cutoff() -> None:
    """2026-07-26是正(A3): 気分の軌跡はSerina日ごとに独立している（_dayタグ）ため、
    旧I-4後追い対処（直前1日分だけ上限を超えて同じtickで通す特例）は不要になり削除した。
    max_countは特例なく厳密な上限として働き、残りは次回tickへ持ち越す。"""
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

    # max_count=2、未処理は3日分。特例が無いので厳密に2件で打ち切る。
    generated = gui_server._run_pending_diaries_for_serina_days(state, timing, now=NOW, max_count=2)

    assert generated == 2, "特例なく上限で打ち切る"
    assert state.last_episodic_at == day3_start, "3日目は次回tickへ持ち越し"
    assert core.emotion.clear_days == ["2026-07-19", "2026-07-20"], "処理した日だけクリアする"


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
    """気分の軌跡へのday引数を記録するスタブ（2026-07-26 A3回帰検査用）。

    実際のEmotionStateはスナップショットに_dayタグを持ち、summarize/clearはそのタグで
    絞り込む。ここでは日ごとに別内容を返すことで「各日は自分の軌跡だけを材料にする」
    ことを検証する。
    """

    def __init__(self) -> None:
        self.summarize_days: list[str | None] = []
        self.clear_days: list[str | None] = []
        self.mood_trajectory: list = []
        self.current_day: str | None = None

    def summarize_trajectory(self, day: str | None = None) -> str:
        self.summarize_days.append(day)
        return f"軌跡:{day}分の記録" if day else ""

    def clear_trajectory(self, day: str | None = None) -> None:
        self.clear_days.append(day)


def test_run_pending_diaries_each_day_uses_only_its_own_trajectory() -> None:
    """2026-07-26是正(A3): 気分の軌跡はSerina日ごとに独立している（_dayタグ）ため、
    キャッチアップの各日がそれぞれ自分のSerina日の軌跡だけを材料にし、消費する。
    旧is_most_recent_pending_day分岐（直前1日分だけ軌跡を使う）は不要になった。"""
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

    expected_days = ["2026-07-19", "2026-07-20", "2026-07-21"]
    assert generated == 3
    assert core.emotion.summarize_days == expected_days
    assert core.emotion.clear_days == expected_days, "生成に成功した全日で該当日だけをクリアする"
    for prompt, day in zip(prompts, expected_days):
        assert f"軌跡:{day}分の記録" in prompt, "各日は自分のSerina日の軌跡だけを材料にする"


def test_post_turn_summary_reentry_skips_when_lock_held() -> None:
    """2026-07-26 A4: 前回の要約スレッドが走行中（Lock保持中）なら、2回目の呼び出しは
    何もせず即座に戻る（同一範囲の二重要約・ターン取りこぼしの防止）。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW)

    calls: list[str] = []
    original = gui_server.run_post_turn_summaries

    def _spy(*args, **kwargs):
        calls.append("called")

    gui_server.run_post_turn_summaries = _spy
    try:
        state.summary_lock.acquire()
        try:
            gui_server._run_post_turn_summaries_async(state)
        finally:
            state.summary_lock.release()
    finally:
        gui_server.run_post_turn_summaries = original

    assert calls == [], "Lock保持中の再入は要約関数を一切呼ばない"


def test_post_turn_summary_runs_when_lock_free() -> None:
    """Lockが空いていれば通常どおり要約関数が呼ばれ、完了後にLockが解放される。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW)

    calls: list[str] = []
    original = gui_server.run_post_turn_summaries

    def _spy(*args, **kwargs):
        calls.append("called")

    gui_server.run_post_turn_summaries = _spy
    try:
        gui_server._run_post_turn_summaries_async(state)
    finally:
        gui_server.run_post_turn_summaries = original

    assert calls == ["called"]
    assert state.summary_lock.acquire(blocking=False), "処理完了後はLockが解放されている"
    state.summary_lock.release()


def test_schedule_candidates_queried_each_pulse_tick() -> None:
    """予定候補は毎tick取得する（窓の開閉が時刻依存のためキャッシュしない）。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    calls = 0

    def _spy_list_schedule(now, state):  # noqa: ANN001
        nonlocal calls
        calls += 1
        return []

    core.list_schedule_pulse_candidates = _spy_list_schedule
    state = _make_state(core, last_activity_at=NOW)

    for _ in range(3):
        gui_server._maybe_fire_pulse_inner(state, now=NOW)

    assert calls == 3


def test_reconcile_skipped_while_conversation_active(tmp_path: Path) -> None:
    """I-4: 会話中は reconcile（DB書き込み）を走らせない。"""
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE

    store = _fresh_store()
    store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="病院",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-20T15:00:00+09:00",
        valid_to="2026-07-20T17:00:00+09:00",
    )
    core = StubCore(_fresh_chore_box(), store, _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    assert state.turn_lock.acquire(blocking=False)
    try:
        gui_server._maybe_fire_pulse_inner(
            state,
            now=datetime(2026, 7, 28, 21, 0, tzinfo=JST),
        )
    finally:
        state.turn_lock.release()
    facts = store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE)
    assert len(facts) == 1
    assert facts[0].status == "active"


def test_reconcile_skipped_while_mute(tmp_path: Path) -> None:
    """I-4: mute 中も reconcile を走らせない。"""
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE

    store = _fresh_store()
    store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="病院",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-20T15:00:00+09:00",
        valid_to="2026-07-20T17:00:00+09:00",
    )
    core = StubCore(_fresh_chore_box(), store, _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    state.pulse_mute = True
    gui_server._maybe_fire_pulse_inner(
        state,
        now=datetime(2026, 7, 28, 21, 0, tzinfo=JST),
    )
    assert store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE)[0].status == "active"


def test_schedule_pulse_state_saved_only_on_change(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """I-4: schedule_pulse_state は変化時のみ保存する。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    saves: list[dict] = []

    def _spy_save(path, *, fired):  # noqa: ANN001
        saves.append(dict(fired))

    monkeypatch.setattr(gui_server, "save_schedule_pulse_state", _spy_save)
    # 変化なし reconcile → 保存しない
    gui_server._maybe_fire_pulse_inner(state, now=NOW)
    assert saves == []


def test_migration_anchor_uses_last_tick_at_not_now() -> None:
    """2026-07-26 A3是正(serina-code-reviewer指摘I-3): 旧mood_trajectory（_day未タグ）
    への付与Serina日は、起動時点(now)ではなく最後にターンを処理した時刻
    (last_tick_at)を錨にする。3日ぶりの起動でも、軌跡は3日前のSerina日に属する
    べきで「今日」に付け替えてはいけない。"""
    jst = ZoneInfo("Asia/Tokyo")
    last_tick_at = datetime(2026, 7, 23, 20, 0, tzinfo=jst).astimezone(timezone.utc)
    now = datetime(2026, 7, 26, 9, 0, tzinfo=jst).astimezone(timezone.utc)  # 3日後に起動

    anchor_day = gui_server._migration_anchor_serina_day(
        {"last_tick_at": last_tick_at.isoformat()}, now=now,
    )

    from serina.core.state.serina_day import serina_day_id

    assert anchor_day == serina_day_id(last_tick_at).isoformat()
    assert anchor_day != serina_day_id(now).isoformat()


def test_migration_anchor_falls_back_to_now_when_no_last_tick_at() -> None:
    """last_tick_at未保存（初回起動等）の場合のみnowにフォールバックする。"""
    from serina.core.state.serina_day import serina_day_id

    now = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    anchor_day = gui_server._migration_anchor_serina_day({}, now=now)
    assert anchor_day == serina_day_id(now).isoformat()


def test_migration_anchor_respects_boundary_hour() -> None:
    """日記キャッチアップと同値のboundary_hourを渡すと日タグが揃う。"""
    from serina.core.state.serina_day import serina_day_id

    jst = ZoneInfo("Asia/Tokyo")
    # 05:30 JST: hour=7なら前日、hour=5なら当日
    last_tick_at = datetime(2026, 7, 26, 5, 30, tzinfo=jst).astimezone(timezone.utc)
    now = datetime(2026, 7, 26, 9, 0, tzinfo=jst).astimezone(timezone.utc)
    anchor = gui_server._migration_anchor_serina_day(
        {"last_tick_at": last_tick_at.isoformat()},
        now=now,
        boundary_hour=5,
    )
    assert anchor == serina_day_id(last_tick_at, boundary_hour=5).isoformat()
    assert anchor != serina_day_id(last_tick_at, boundary_hour=7).isoformat()


def test_maybe_run_serina_day_boundary_inner_skips_before_first_turn(monkeypatch) -> None:  # noqa: ANN001
    """2026-08-01是正: マスターの最初の発言がまだ来ていない間は日界処理を走らせない
    （起動直後・待機中にセッションが切り替わる、というマスター報告への対策）。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW - timedelta(minutes=20),
        last_boundary_serina_day=date(2026, 7, 21),
        has_had_first_turn=False,
    )
    timing = _timing()
    monkeypatch.setattr(
        gui_server, "run_startup_chores",
        lambda *a, **k: MagicMock(processed=0, failed=[], total_accepted=0, shelved=[]),
    )
    monkeypatch.setattr(gui_server, "_run_growth_chores_for_state", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "_run_pending_diaries_for_serina_days", lambda *a, **k: 0)

    gui_server._maybe_run_serina_day_boundary_inner(state, timing, now=NOW)

    assert core.end_session_calls == 0
    state.session_mgr.rotate.assert_not_called()
    assert state.last_boundary_serina_day == date(2026, 7, 21)


def test_maybe_fire_pulse_inner_skips_before_first_turn() -> None:
    """2026-08-01是正: マスターの最初の発言がまだ来ていない間はPulseを発火させない。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW, has_had_first_turn=False)

    gui_server._maybe_fire_pulse_inner(state, now=NOW)

    assert state.pulse_queue == []


def test_run_startup_morning_routine_updates_boundary_day_and_prevents_double_fire(monkeypatch) -> None:  # noqa: ANN001
    """2026-08-01是正の回帰テスト。

    起動時朝礼(run_startup_morning_routine)を経ると last_boundary_serina_day が
    今日の日付に更新され、直後に見回いスレッド(_watchdog_tick_at)を15分アイドル経過状態で
    走らせても、日界処理（特にセッション切替）が重複発火しない。旧実装は起動時朝礼が
    last_boundary_serina_dayを更新しなかったため、この重複発火が実際に起きていた。
    """
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core,
        last_activity_at=NOW,
        last_boundary_serina_day=date(2026, 7, 20),
        has_had_first_turn=True,
    )
    timing = _timing()

    monkeypatch.setattr(
        gui_server, "run_startup_chores",
        lambda *a, **k: MagicMock(processed=0, failed=[], total_accepted=0, shelved=[]),
    )
    monkeypatch.setattr(gui_server, "_run_growth_chores_for_state", lambda *a, **k: None)
    monkeypatch.setattr(gui_server, "_run_pending_diaries_for_serina_days", lambda *a, **k: 1)
    monkeypatch.setattr(gui_server, "should_generate_diary_at_startup", lambda **k: True)

    gui_server.run_startup_morning_routine(state, timing, now=NOW)

    expected_day = gui_server.serina_day_id(NOW, boundary_hour=timing.serina_day_boundary_hour)
    assert state.last_boundary_serina_day == expected_day

    # 直後、15分アイドル経過状態で見回いスレッドを走らせても、もう発火しない
    state.last_activity_at = NOW - timedelta(minutes=20)
    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    monkeypatch.setattr(gui_server, "_maybe_fire_pulse", lambda *a, **k: None)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 0
    state.session_mgr.rotate.assert_not_called()


def test_resync_episodic_state_after_delete_does_not_rewind() -> None:
    """2026-08-01是正の回帰テスト。

    2026-07-23に最新のepisodicを削除→水位が2025年の残存最古まで巻き戻り、
    2026-08-01に複数日分の日記が一斉に再生成される事故が実データで発生した。
    削除後に残る最新episodicが、削除前のlast_episodic_atより古い場合、
    水位は後退してはならない。
    """
    store = _fresh_store()
    store.add_memory(
        "古い日記（レガシー相当）",
        type="episodic",
        importance=0.8,
        sensitivity_grade=2,
        protection_grade="A",
        created_at="2025-04-06T22:00:00+00:00",
    )
    core = StubCore(_fresh_chore_box(), store, _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    newer = datetime(2026, 7, 31, 22, 0, tzinfo=timezone.utc)
    state.last_episodic_at = newer

    gui_server._resync_episodic_state_after_delete(state)

    assert state.last_episodic_at == newer


def test_resync_episodic_state_after_delete_advances_to_remaining_newest() -> None:
    """通常ケース: 削除後に残る最新episodicが、削除前のlast_episodic_atより新しければ前進する
    （後退防止ガードが前進まで塞いでいないことの確認）。"""
    store = _fresh_store()
    store.add_memory(
        "新しい日記",
        type="episodic",
        importance=0.8,
        sensitivity_grade=2,
        protection_grade="A",
        created_at="2026-08-01T22:00:00+00:00",
    )
    core = StubCore(_fresh_chore_box(), store, _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    older = datetime(2026, 7, 20, 22, 0, tzinfo=timezone.utc)
    state.last_episodic_at = older

    gui_server._resync_episodic_state_after_delete(state)

    assert state.last_episodic_at == datetime(2026, 8, 1, 22, 0, tzinfo=timezone.utc)


def test_resync_episodic_state_after_delete_no_remaining_falls_back_to_now() -> None:
    """全episodicを消した場合はnowにフォールバックする（既存挙動を維持。
    後退防止ガードはmax()なのでnow >= last_episodic_atの通常状態では影響しない）。"""
    store = _fresh_store()
    core = StubCore(_fresh_chore_box(), store, _thresholds())
    state = _make_state(core, last_activity_at=NOW)
    older = datetime(2020, 1, 1, tzinfo=timezone.utc)  # 十分に古い過去
    state.last_episodic_at = older

    before_call = datetime.now(timezone.utc)
    gui_server._resync_episodic_state_after_delete(state)
    after_call = datetime.now(timezone.utc)

    assert before_call <= state.last_episodic_at <= after_call
