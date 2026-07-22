"""app/gui_server.py の起動レベル疎通テスト。設計書 §2.4。

advisorレビュー2026-07-11「見回りスレッドが例外を毎tick飲み込むと、サーバは落ちずに
アイドル消化だけ永遠に動かない、という沈黙する失敗モードがある」への対応。
main()はOllama必須(MemoryStore用embedder)のため丸ごとは呼ばず、STATEを直接組み立てて
- 見回りスレッド(_idle_watchdog)を実際に起動し、1tick以上生き延びて例外ログを出さないか
を確認する。Ollama/Qwen不要（StubCore・フェイクembedderのみ使用）。

心拍(heartbeat)によるGUI終了検知は死に枝と判明し2026-07-12に廃止した（DECISIONS参照）。
"""

from __future__ import annotations

import logging
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.app import gui_server
from serina.app.idle_config import AppTimingConfig
from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore


class _StubCore:
    def __init__(self, chore_box: ChoreBox, memory_store: MemoryStore, thresholds: ThresholdsConfig) -> None:
        self.chore_box = chore_box
        self.memory_store = memory_store
        self.thresholds = thresholds
        self.end_session_calls = 0

    def end_session(self) -> list[int]:
        self.end_session_calls += 1
        return []


def _fresh_store() -> MemoryStore:
    embedder = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    return MemoryStore(str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=embedder, vector_dim=4)


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _install_stub_state() -> gui_server.GuiState:
    core = _StubCore(_fresh_chore_box(), _fresh_store(), ThresholdsConfig(
        fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1,
    ))
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    state.session_store = None
    state.session_mgr = None
    state.session_id = "s_smoke"
    state.turn_lock = threading.Lock()
    state.lane_call_fns = {}
    tmp = Path(tempfile.mkdtemp())
    now = datetime.now(timezone.utc)
    state.last_activity_at = now
    state.session_ended = False
    state.watchdog_lock = threading.Lock()
    state.last_diary_at = now
    state.diary_state_path = tmp / "diary_state.json"
    state.serina_boundary_state_path = tmp / "serina_boundary_state.json"
    state.last_boundary_serina_day = now.astimezone().date()
    state.pulse_state_path = tmp / "pulse.json"
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    gui_server.STATE = state
    return state


def test_idle_watchdog_survives_several_ticks_without_exception(caplog) -> None:  # noqa: ANN001
    """見回りスレッドを実際に起動し、複数tick後も生きていて例外ログが出ていないことを確認する
    （沈黙する失敗モード対策: exceptで握りつぶされるとログにしか痕跡が残らない）。
    """
    state = _install_stub_state()
    timing = AppTimingConfig(
        idle_timeout_after_seconds=9999,
        idle_digest_gap_seconds=9999,
        idle_poll_interval_seconds=0.05,  # type: ignore[arg-type]  # テストのみ高速化のため小数許容
        idle_digest_chunk_limit=1,
        gpu_busy_threshold_percent=40.0,
    )

    caplog.set_level(logging.ERROR, logger="serina.app.gui_server")
    thread = threading.Thread(target=gui_server._idle_watchdog, args=(state, timing), daemon=True)
    thread.start()
    time.sleep(0.3)  # poll_interval=0.05sで複数tick回るのに十分

    assert thread.is_alive()  # 見回りスレッド自体は死んでいない(daemon threadなので明示終了はしない)
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error_records == [], f"見回りスレッドが例外を飲み込んでいる: {[r.message for r in error_records]}"


def main() -> None:
    failed = 0

    # caplog相当を自前ロガーハンドラで代用(pytest不要の自前ランナーのため)
    class _Collector(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.ERROR)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    collector = _Collector()
    gui_server.logger.addHandler(collector)
    try:
        state = _install_stub_state()
        timing = AppTimingConfig(
            idle_timeout_after_seconds=9999,
            idle_digest_gap_seconds=9999,
            idle_poll_interval_seconds=0.05,  # type: ignore[arg-type]
            idle_digest_chunk_limit=1,
            gpu_busy_threshold_percent=40.0,
        )
        thread = threading.Thread(target=gui_server._idle_watchdog, args=(state, timing), daemon=True)
        thread.start()
        time.sleep(0.3)
        assert thread.is_alive()
        assert collector.records == [], f"見回りスレッドが例外を飲み込んでいる: {[r.getMessage() for r in collector.records]}"
        print("  [OK] test_idle_watchdog_survives_several_ticks_without_exception")
    except AssertionError as e:
        failed += 1
        print(f"  [NG] test_idle_watchdog_survives_several_ticks_without_exception: {e}")
    finally:
        gui_server.logger.removeHandler(collector)

    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
