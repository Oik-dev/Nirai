"""アイドル時トリガーの判定ロジックのテスト。設計書 §2.4。

core/chores/idle_policy.py の純粋関数(should_run_idle_chores)と
core/chores/gpu_guard.py のfail-open動作を検査する。タイマ・スレッド不要
（advisorレビュー2026-07-11: sleep依存を避けるため純粋関数に切り出した効果の検証）。

2026-07-26 A9: deprecated関数（decide_session_end/should_digest。旧無操作タイムアウト
トリガー・旧アイドル内職ゲート。どちらもGUI本体からは未参照）を削除したのに伴い、
対応テストも削除した。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.gpu_guard import is_gpu_busy
from serina.core.chores.idle_policy import should_run_idle_chores

NOW = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)


def test_should_run_idle_chores_only_when_session_ended() -> None:
    assert should_run_idle_chores(session_ended=False) is False
    assert should_run_idle_chores(session_ended=True) is True


def test_is_gpu_busy_fails_open_without_nvidia_smi() -> None:
    """nvidia-smiが無い/失敗する環境ではFalse(=見送らず継続)を返す(fail-open)。
    CI・大半の開発機はnvidia-smi不在のため、この呼び出し自体が既に検証になる。
    """
    result = is_gpu_busy(threshold_percent=1_000_000.0)  # どんな実環境でも閾値未満のはず
    assert result is False


def main() -> None:
    tests = [
        test_should_run_idle_chores_only_when_session_ended,
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
