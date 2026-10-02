"""episodic記憶生成の永続状態(core/state/episodic_state.py)のテスト。設計書 §4.5(2026-07-12改訂)。

last_episodic_at・気分の軌跡が電源断をまたいで永続化されることを検査する
（旧設計はプロセス内メモリのみで、この永続化が無いと「夜に会話→電源断」運用で
episodic記憶が一度も生成されない構造欠陥があった。DECISIONS参照）。

2026-07-23: 旧`diary_state.py`から改名。旧ファイル（`data/diary_state.json`・キー
`last_diary_at`）が残っている環境向けフォールバック読み込みも検査する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.state.episodic_state import load_episodic_state, save_episodic_state


def _fresh_path() -> Path:
    return Path(tempfile.mkdtemp()) / "episodic_state.json"


def test_load_episodic_state_without_file_initializes_to_now_and_empty_trajectory(monkeypatch) -> None:
    """1度目の起動（新ファイル・旧ファイルとも無し）では「今まさに起動した」扱いにする
    （過去分を遡ってepisodic化しようとしない）。

    実プロジェクトの`data/diary_state.json`（マスターの実運用データ）へフォールバックしない
    よう、旧ファイルパスも存在しないtmpパスへ差し替えて隔離する。
    """
    import serina.core.state.episodic_state as episodic_state_module

    monkeypatch.setattr(
        episodic_state_module, "_LEGACY_STATE_PATH", Path(tempfile.mkdtemp()) / "diary_state.json",
    )
    before = datetime.now(timezone.utc)
    last_episodic_at, mood_trajectory = load_episodic_state(_fresh_path())
    after = datetime.now(timezone.utc)

    assert before <= last_episodic_at <= after
    assert mood_trajectory == []


def test_save_and_load_round_trip_survives_reopen() -> None:
    path = _fresh_path()
    last_episodic_at = datetime(2026, 7, 11, 22, 0, 0, tzinfo=timezone.utc)
    mood_trajectory = [{"喜び": 0.3}, {"喜び": 0.5}]

    save_episodic_state(path, last_episodic_at=last_episodic_at, mood_trajectory=mood_trajectory)
    loaded_at, loaded_trajectory = load_episodic_state(path)  # プロセス再起動を模した再読み込み

    assert loaded_at == last_episodic_at
    assert loaded_trajectory == mood_trajectory


def test_load_falls_back_to_legacy_diary_state_json_when_new_file_missing(monkeypatch) -> None:
    """新ファイルが無く旧`diary_state.json`だけがある環境で、電源断耐性の継続性を失わない
    ことを検査する（旧キー`last_diary_at`を読み替える）。"""
    import serina.core.state.episodic_state as episodic_state_module

    tmp_dir = Path(tempfile.mkdtemp())
    legacy_path = tmp_dir / "diary_state.json"
    legacy_last_at = datetime(2026, 7, 20, 9, 0, 0, tzinfo=timezone.utc)
    legacy_path.write_text(
        json.dumps({"last_diary_at": legacy_last_at.isoformat(), "mood_trajectory": [{"安心": 0.4}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(episodic_state_module, "_LEGACY_STATE_PATH", legacy_path)

    new_path = tmp_dir / "episodic_state.json"  # まだ存在しない
    loaded_at, loaded_trajectory = load_episodic_state(new_path)

    assert loaded_at == legacy_last_at
    assert loaded_trajectory == [{"安心": 0.4}]


def main() -> None:
    tests = [
        test_load_episodic_state_without_file_initializes_to_now_and_empty_trajectory,
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
        print("全テスト合格（pytest限定テストのlegacy fallbackはmain()未実行、pytestで確認すること）")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
