"""日記生成の永続状態(core/state/diary_state.py)のテスト。設計書 §4.5(2026-07-12改訂)。

last_diary_at・気分の軌跡が電源断をまたいで永続化されることを検査する
（旧設計はプロセス内メモリのみで、この永続化が無いと「夜に会話→電源断」運用で
日記が一度も生成されない構造欠陥があった。DECISIONS参照）。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.state.diary_state import load_diary_state, save_diary_state


def _fresh_path() -> Path:
    return Path(tempfile.mkdtemp()) / "diary_state.json"


def test_load_diary_state_without_file_initializes_to_now_and_empty_trajectory() -> None:
    """1度目の起動（ファイル無し）では「今まさに起動した」扱いにする
    （過去分を遡って日記化しようとしない）。"""
    before = datetime.now(timezone.utc)
    last_diary_at, mood_trajectory = load_diary_state(_fresh_path())
    after = datetime.now(timezone.utc)

    assert before <= last_diary_at <= after
    assert mood_trajectory == []


def test_save_and_load_round_trip_survives_reopen() -> None:
    path = _fresh_path()
    last_diary_at = datetime(2026, 7, 11, 22, 0, 0, tzinfo=timezone.utc)
    mood_trajectory = [{"喜び": 0.3}, {"喜び": 0.5}]

    save_diary_state(path, last_diary_at=last_diary_at, mood_trajectory=mood_trajectory)
    loaded_at, loaded_trajectory = load_diary_state(path)  # プロセス再起動を模した再読み込み

    assert loaded_at == last_diary_at
    assert loaded_trajectory == mood_trajectory


def main() -> None:
    tests = [
        test_load_diary_state_without_file_initializes_to_now_and_empty_trajectory,
        test_save_and_load_round_trip_survives_reopen,
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
