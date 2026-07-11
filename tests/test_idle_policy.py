"""アイドル時トリガーの判定ロジックのテスト。設計書v2 §2.4。

core_v2/chores/idle_policy.py の純粋関数(decide_session_end/should_digest)と
core_v2/chores/gpu_guard.py のfail-open動作を検査する。タイマ・スレッド不要
（advisorレビュー2026-07-11: sleep依存を避けるため純粋関数に切り出した効果の検証）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.chores.gpu_guard import is_gpu_busy
from serina.core_v2.chores.idle_policy import decide_session_end, should_digest

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def _ago(seconds: float) -> datetime:
    return NOW - timedelta(seconds=seconds)


def test_decide_session_end_none_when_all_recent() -> None:
    d = decide_session_end(
        now=NOW, last_heartbeat_at=_ago(5), last_activity_at=_ago(5),
        session_ended=False, heartbeat_lost_after_seconds=300, idle_timeout_after_seconds=300,
    )
    assert d.should_end is False
    assert d.reason is None


def test_decide_session_end_heartbeat_lost() -> None:
    """トリガー1: 心拍が閾値以上途絶えたらGUI終了とみなす。"""
    d = decide_session_end(
        now=NOW, last_heartbeat_at=_ago(301), last_activity_at=_ago(301),
        session_ended=False, heartbeat_lost_after_seconds=300, idle_timeout_after_seconds=300,
    )
    assert d.should_end is True
    assert d.reason == "heartbeat_lost"


def test_decide_session_end_idle_timeout_with_heartbeat_alive() -> None:
    """トリガー2: 心拍は生きている(画面は開いたまま)が無操作が続いたらタイムアウト。"""
    d = decide_session_end(
        now=NOW, last_heartbeat_at=_ago(5), last_activity_at=_ago(301),
        session_ended=False, heartbeat_lost_after_seconds=300, idle_timeout_after_seconds=300,
    )
    assert d.should_end is True
    assert d.reason == "idle_timeout"


def test_decide_session_end_already_ended_stays_false() -> None:
    """end_session()の二重呼び出し防止: 既にsession_endedならもう終了判定しない。"""
    d = decide_session_end(
        now=NOW, last_heartbeat_at=_ago(9999), last_activity_at=_ago(9999),
        session_ended=True, heartbeat_lost_after_seconds=300, idle_timeout_after_seconds=300,
    )
    assert d.should_end is False


def test_should_digest_false_when_gap_too_short() -> None:
    assert should_digest(now=NOW, last_activity_at=_ago(5), session_ended=False, digest_gap_seconds=60) is False


def test_should_digest_true_when_gap_long_enough() -> None:
    assert should_digest(now=NOW, last_activity_at=_ago(61), session_ended=False, digest_gap_seconds=60) is True


def test_should_digest_true_when_session_already_ended() -> None:
    """終了後は会話が戻る心配がないため、間隔を待たず毎ティック消化を試みてよい。"""
    assert should_digest(now=NOW, last_activity_at=_ago(1), session_ended=True, digest_gap_seconds=60) is True


def test_is_gpu_busy_fails_open_without_nvidia_smi() -> None:
    """nvidia-smiが無い/失敗する環境ではFalse(=見送らず継続)を返す(fail-open)。
    CI・大半の開発機はnvidia-smi不在のため、この呼び出し自体が既に検証になる。
    """
    result = is_gpu_busy(threshold_percent=1_000_000.0)  # どんな実環境でも閾値未満のはず
    assert result is False


def main() -> None:
    tests = [
        test_decide_session_end_none_when_all_recent,
        test_decide_session_end_heartbeat_lost,
        test_decide_session_end_idle_timeout_with_heartbeat_alive,
        test_decide_session_end_already_ended_stays_false,
        test_should_digest_false_when_gap_too_short,
        test_should_digest_true_when_gap_long_enough,
        test_should_digest_true_when_session_already_ended,
        test_is_gpu_busy_fails_open_without_nvidia_smi,
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
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
