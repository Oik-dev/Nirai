"""感情状態の永続化テスト。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.state.emotion import EmotionState
from serina.core.state.emotion_persist import (
    apply_loaded_to_emotion,
    load_emotion_state,
    save_emotion_state,
    snapshot_from_emotion,
)


def test_emotion_persist_roundtrip() -> None:
    tmp = Path(tempfile.mkdtemp()) / "emotion_state.json"
    state = EmotionState()
    state.affect["怒り"] = 0.8
    state.mood["悲しみ"] = 0.3
    state.last_tick_at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    snap = snapshot_from_emotion(state)
    save_emotion_state(
        tmp,
        affect=snap["affect"],
        mood=snap["mood"],
        last_tick_at=state.last_tick_at,
    )

    loaded = EmotionState()
    apply_loaded_to_emotion(loaded, load_emotion_state(tmp))
    assert loaded.affect["怒り"] == 0.8
    assert loaded.mood["悲しみ"] == 0.3
    assert loaded.last_tick_at == state.last_tick_at
