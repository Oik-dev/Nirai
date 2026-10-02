"""予定窓の発火済みフラグ永続化テスト（Task 1-3）。"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.schedule_pulse_state import (
    clear_fired_keys_for_fact,
    is_window_fired,
    load_schedule_pulse_state,
    record_window_fire,
    save_schedule_pulse_state,
)


def test_same_window_does_not_fire_twice(tmp_path: Path) -> None:
    path = tmp_path / "schedule_pulse_state.json"
    state = load_schedule_pulse_state(path)
    assert not is_window_fired(state, "fact-1", "直前")

    now = datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc)
    state = record_window_fire(state, fact_id="fact-1", window="直前", fired_at=now)
    save_schedule_pulse_state(path, fired=state["fired"])

    reloaded = load_schedule_pulse_state(path)
    assert is_window_fired(reloaded, "fact-1", "直前")
    assert not is_window_fired(reloaded, "fact-1", "事後")


def test_fire_record_survives_across_days(tmp_path: Path) -> None:
    path = tmp_path / "schedule_pulse_state.json"
    day1 = datetime(2026, 7, 27, 19, 0, tzinfo=timezone.utc)
    state = record_window_fire(
        load_schedule_pulse_state(path),
        fact_id="ann-1",
        window="前夜",
        fired_at=day1,
    )
    save_schedule_pulse_state(path, fired=state["fired"])

    # 日をまたいでもファイルから復元される
    later = day1 + timedelta(days=2)
    del later  # 時刻そのものは問わず、永続化の保持を見る
    reloaded = load_schedule_pulse_state(path)
    assert is_window_fired(reloaded, "ann-1", "前夜")
    assert reloaded["fired"]["ann-1:前夜"] == day1.isoformat()


def test_clear_fired_keys_for_fact() -> None:
    state = {
        "fired": {
            "a:前夜": "2026-07-06T19:00:00+00:00",
            "a:直前": "2026-07-07T14:00:00+00:00",
            "b:前夜": "2026-07-06T19:00:00+00:00",
        }
    }
    cleared = clear_fired_keys_for_fact(state, "a")
    assert "a:前夜" not in cleared["fired"]
    assert "a:直前" not in cleared["fired"]
    assert cleared["fired"]["b:前夜"] == "2026-07-06T19:00:00+00:00"
