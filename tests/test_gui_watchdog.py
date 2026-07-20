"""app/gui_server.py の見回りスレッド本体(_watchdog_tick)のテスト。設計書 §2.4。

decide_session_end/should_digest(core/chores/idle_policy.py)は純粋関数として別途
テスト済み(tests/test_idle_policy.py)。ここではその判定結果を実際にどう使うか
——end_session()の二重発火防止・GPU番人・turn_lockの取り合い・end→digestの同ティック内順序
——というtick側の配線をスタブCoreで検査する（advisorレビュー2026-07-11:
「テスト容易性のために割ったのに割った先をテストしていない」の指摘を受けて追加）。
Ollama/Qwen不要（call_fnをスタブ化）。
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

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

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


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
        self.quota_ledger = QuotaLedger()  # 2026-07-12追加: cloud車線ジョブの残弾ゲート用

    def end_session(self) -> list[int]:
        self.end_session_calls += 1
        self.session = SessionState()
        return []


class _StubEmotion:
    """§4.5日記生成用の最小スタブ。EmotionState本体はtest_diary_generation.pyで別途検査済み。"""

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
        idle_timeout_after_seconds=9999,
        idle_digest_gap_seconds=10,
        idle_poll_interval_seconds=20,
        idle_digest_chunk_limit=1,
        export_life_min_interval_seconds=999_999,
        gpu_busy_threshold_percent=40.0,
    )
    base.update(overrides)
    return AppTimingConfig(**base)


def _make_state(core: StubCore, *, last_activity_at: datetime, session_ended: bool = False) -> gui_server.GuiState:
    state = gui_server.GuiState.__new__(gui_server.GuiState)  # __init__のlane_call_fns構築(実アダプタ生成)を避ける
    state.core = core
    state.session_store = None
    state.session_mgr = None
    state.session_id = "s_test"
    state.turn_lock = threading.Lock()
    state.lane_call_fns = {"local": _stub_call_fn}
    tmp = Path(tempfile.mkdtemp())
    state.change_log = ChangeLog(tmp / "changes.jsonl")
    from serina.core.memory.protection import GenerationStore

    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.db_path = None  # 既存テストでは export_life を走らせない
    state.life_dir = None
    state.summaries_path = None
    state.last_export_life_at = None
    state.last_activity_at = last_activity_at
    state.session_ended = session_ended
    state.watchdog_lock = threading.Lock()
    # §4.5夜間放出: 「直前に生成した」扱いにして、既存テストではmin_gap未達により発火させない
    # （このtickの主眼はend/digestの配線であり、diary発火の検証はtest_diary_generation.py側）。
    state.last_diary_at = last_activity_at
    state.last_diary_empty_at = None
    state.pulse_state_path = tmp / "pulse.json"
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    return state


def _stub_call_fn(prompt: str) -> str:
    return json.dumps({"candidates": []})


def test_tick_fires_end_once_on_idle_timeout() -> None:
    """旧トリガー1(心拍途絶)は死に枝と判明し2026-07-12に廃止（DECISIONS参照）。
    現在は無操作タイムアウト単独でセッション終了を判定する。"""
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=10_000))
    timing = _timing(idle_timeout_after_seconds=300)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 1
    assert state.session_ended is True


def test_tick_does_not_refire_once_already_ended() -> None:
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    state = _make_state(
        core, last_activity_at=NOW - timedelta(seconds=10_000),
        session_ended=True,
    )
    timing = _timing(idle_timeout_after_seconds=300)

    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 0  # 既にended: idle_policy.decide_session_endがFalseを返す


def test_tick_skips_digest_when_gpu_busy(monkeypatch) -> None:  # noqa: ANN001
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=100), session_ended=True)
    timing = _timing()

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: True)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert len(box.pending(kind="蒸留")) == 1  # GPU多忙のため見送り、宿題箱に残る


def test_tick_skips_digest_when_turn_lock_held() -> None:
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=100), session_ended=True)
    timing = _timing()

    release = threading.Event()
    holder = threading.Thread(target=lambda: (state.turn_lock.acquire(), release.wait(2), state.turn_lock.release()))
    holder.start()
    time.sleep(0.05)  # holderが確実にロックを握るのを待つ
    try:
        gui_server._watchdog_tick_at(state, timing, now=NOW)
        assert len(box.pending(kind="蒸留")) == 1  # 会話優先: 非ブロッキング取得に失敗し見送り
    finally:
        release.set()
        holder.join()


def test_tick_recheck_cancels_end_when_activity_resumes_during_lock_wait() -> None:
    """advisorレビュー2026-07-11で最重要指摘: 見回りがturn_lock待ちしている間に会話が
    再開したら、終了処理(end_session)を取り消さねばならない（さもないと会話直後にセッションを
    誤リセットしてしまう。§2.4「会話が常に優先」の一番重い帰結）。

    実シナリオの再現: 見回りが「終了すべき」と一次判定→turn_lock待ち中に
    マスターが話しかけて_produce_turnがロックを握り、活動時刻を更新してから解放する
    →見回りがロック取得後にrecheckし、更新済みの活動時刻を見て終了を取り消す。
    """
    core = StubCore(_fresh_chore_box(), _fresh_store(), _thresholds())
    stale = NOW - timedelta(seconds=10_000)
    state = _make_state(core, last_activity_at=stale)
    timing = _timing(idle_timeout_after_seconds=300)

    def _simulate_conversation_during_lock_wait() -> None:
        state.turn_lock.acquire()
        time.sleep(0.15)  # tick側が一次判定を終えturn_lock待ちに入るのを待つ
        with state.watchdog_lock:
            resumed = datetime.now(timezone.utc)
            state.last_activity_at = resumed
            state.session_ended = False
        state.turn_lock.release()

    holder = threading.Thread(target=_simulate_conversation_during_lock_wait)
    holder.start()
    time.sleep(0.05)  # holderが確実にturn_lockを先に握るのを待つ

    gui_server._watchdog_tick_at(state, timing, now=NOW)
    holder.join()

    assert core.end_session_calls == 0  # recheckが取り消した
    assert state.session_ended is False  # 会話再開後の状態を上書きしていない


def test_tick_digests_one_job_when_conditions_met(monkeypatch) -> None:  # noqa: ANN001
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "もう1件"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=100), session_ended=True)
    timing = _timing(idle_digest_chunk_limit=1)

    # 実GPU状態に依存しない（MILESTONE残・DECISIONS 2026-07-11）
    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert len(box.pending(kind="蒸留")) == 1  # limit=1で1件だけ消化・1件は次回へ


def test_tick_no_digest_during_active_session(monkeypatch) -> None:  # noqa: ANN001
    """§3.8: セッション継続中は宿題があっても digest しない。"""
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=100), session_ended=False)
    timing = _timing()

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert len(box.pending(kind="蒸留")) == 1


def test_tick_end_and_digest_run_in_same_tick(monkeypatch) -> None:  # noqa: ANN001
    """終了トリガーが発火したティックでも、同じティック内で②の消化まで進む
    (should_digestはsession_ended=True後すぐTrueを返す仕様。DECISIONS 2026-07-11)。
    """
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "テスト"}]})
    core = StubCore(box, _fresh_store(), _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=10_000))
    timing = _timing(idle_timeout_after_seconds=300)

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert core.end_session_calls == 1
    assert box.pending(kind="蒸留") == []  # 同ティックで消化まで完了


def test_tick_switches_legacy_cloud_job_to_local(monkeypatch) -> None:  # noqa: ANN001
    """レガシー cloud 蒸留ジョブは local へ振替して消化する（quota 経路は退役）。"""
    box = _fresh_chore_box()
    box.enqueue("蒸留", lane="cloud", payload={"turns": [{"speaker": "master", "text": "クラウド宿題"}]})
    store = _fresh_store()
    core = StubCore(box, store, _thresholds())
    state = _make_state(core, last_activity_at=NOW - timedelta(seconds=100), session_ended=True)
    state.lane_call_fns = {
        "local": lambda _p: json.dumps({"candidates": []}),
    }
    timing = _timing(idle_digest_chunk_limit=1)

    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda threshold: False)
    gui_server._watchdog_tick_at(state, timing, now=NOW)

    assert box.pending(kind="蒸留") == []


def main() -> None:
    tests = [
        test_tick_fires_end_once_on_idle_timeout,
        test_tick_does_not_refire_once_already_ended,
        test_tick_skips_digest_when_turn_lock_held,
        test_tick_recheck_cancels_end_when_activity_resumes_during_lock_wait,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")

    # monkeypatch引数を使うテストは簡易スタブで実行(pytest不要の自前ランナーのため)
    mp_tests = [
        ("test_tick_skips_digest_when_gpu_busy", test_tick_skips_digest_when_gpu_busy),
        ("test_tick_no_digest_during_active_session", test_tick_no_digest_during_active_session),
        ("test_tick_digests_one_job_when_conditions_met", test_tick_digests_one_job_when_conditions_met),
        ("test_tick_end_and_digest_run_in_same_tick", test_tick_end_and_digest_run_in_same_tick),
        (
            "test_tick_switches_legacy_cloud_job_to_local",
            test_tick_switches_legacy_cloud_job_to_local,
        ),
    ]
    for name, fn in mp_tests:
        mp = _MonkeyPatch()
        try:
            fn(mp)
            print(f"  [OK] {name}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {name}: 予期せぬ例外 {type(e).__name__}: {e}")
        finally:
            mp.undo()

    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


class _MonkeyPatch:
    def __init__(self) -> None:
        self._restores: list[tuple[object, str, object]] = []

    def setattr(self, obj: object, name: str, value: object) -> None:
        self._restores.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self) -> None:
        for obj, name, old in reversed(self._restores):
            setattr(obj, name, old)


if __name__ == "__main__":
    main()
