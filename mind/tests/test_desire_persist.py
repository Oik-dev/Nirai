"""欲求状態の永続化テスト（Task 3-4）。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.desire import DesireState
from mind.core.state.desire_persist import (
    apply_loaded_to_desire,
    load_desire_state,
    save_desire_from_state,
    save_desire_state,
    snapshot_from_desire,
)


def test_desire_persist_roundtrip() -> None:
    tmp = Path(tempfile.mkdtemp()) / "desire_state.json"
    state = DesireState()
    state.level = 0.72
    state.last_tick_at = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    state.refractory_until = state.last_tick_at + timedelta(seconds=129_600)
    snap = snapshot_from_desire(state)
    save_desire_state(
        tmp,
        level=snap["level"],
        refractory_until=snap["refractory_until"],
        last_tick_at=snap["last_tick_at"],
    )

    loaded = DesireState()
    apply_loaded_to_desire(loaded, load_desire_state(tmp))
    assert loaded.level == 0.72
    assert loaded.last_tick_at == state.last_tick_at
    assert loaded.refractory_until == state.refractory_until
    # 2026-07-30: 起動直後の最初のtickより前でもgate.pyの判定が正しく動くよう、
    # 復元時にlevel_before_tickもlevelへ揃えているはず（I-a是正）。
    assert loaded.level_before_tick == loaded.level


def test_desire_persist_missing_file_returns_defaults() -> None:
    tmp = Path(tempfile.mkdtemp()) / "missing_desire.json"
    data = load_desire_state(tmp)
    assert data["level"] == 0.0
    assert data["refractory_until"] is None
    assert data["last_tick_at"] is None


def test_desire_persist_via_save_from_state() -> None:
    tmp = Path(tempfile.mkdtemp()) / "desire_state.json"
    state = DesireState()
    state.level = 0.4
    state.last_tick_at = datetime(2026, 7, 27, 8, 0, tzinfo=timezone.utc)
    save_desire_from_state(tmp, state)

    restored = DesireState()
    apply_loaded_to_desire(restored, load_desire_state(tmp))
    assert restored.level == 0.4
    assert restored.last_tick_at == state.last_tick_at
    assert restored.refractory_until is None


def test_desire_persist_naive_iso_gets_utc() -> None:
    """naive ISO は UTC 補完（emotion/relationship と同型）。"""
    tmp = Path(tempfile.mkdtemp()) / "desire_state.json"
    tmp.write_text(
        '{"level": 0.5, "refractory_until": "2026-07-28T00:00:00",'
        ' "last_tick_at": "2026-07-27T12:00:00"}',
        encoding="utf-8",
    )
    loaded = DesireState()
    apply_loaded_to_desire(loaded, load_desire_state(tmp))
    assert loaded.last_tick_at is not None
    assert loaded.last_tick_at.tzinfo == timezone.utc
    assert loaded.refractory_until is not None
    assert loaded.refractory_until.tzinfo == timezone.utc
