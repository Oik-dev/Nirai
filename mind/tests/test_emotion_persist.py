"""感情状態の永続化テスト。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.emotion import EmotionState
from mind.core.state.emotion_persist import (
    apply_loaded_to_emotion,
    load_emotion_state,
    save_emotion_from_state,
    save_emotion_state,
    snapshot_from_emotion,
)


def test_emotion_persist_roundtrip() -> None:
    tmp = Path(tempfile.mkdtemp()) / "emotion_state.json"
    state = EmotionState(baselines={"喜び": 0.15, "信頼": 0.2})
    state.affect["怒り"] = 0.8
    state.mood["悲しみ"] = 0.3
    state.baseline["喜び"] = 0.35
    state.last_tick_at = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)
    snap = snapshot_from_emotion(state)
    save_emotion_state(
        tmp,
        affect=snap["affect"],
        mood=snap["mood"],
        baseline=snap["baseline"],
        last_tick_at=state.last_tick_at,
    )

    loaded = EmotionState()
    apply_loaded_to_emotion(loaded, load_emotion_state(tmp))
    assert loaded.affect["怒り"] == 0.8
    assert loaded.mood["悲しみ"] == 0.3
    assert loaded.baseline["喜び"] == 0.35
    assert loaded.last_tick_at == state.last_tick_at


def test_fresh_install_keeps_ctor_baseline_when_no_state_file() -> None:
    """2026-07-30 レビューI-b再発防止: 状態ファイルが存在しない新規環境でも、
    コンストラクタで注入したconfig初期値（[emotion_baseline]）が全軸0.0に
    上書きされないこと。"""
    tmp = Path(tempfile.mkdtemp()) / "missing_emotion_state.json"
    state = EmotionState(baselines={"喜び": 0.15, "信頼": 0.2, "期待": 0.1})
    apply_loaded_to_emotion(state, load_emotion_state(tmp))
    assert state.baseline["喜び"] == 0.15
    assert state.baseline["信頼"] == 0.2
    assert state.baseline["期待"] == 0.1


def test_baseline_persists_across_reload() -> None:
    """Task 2-1: 再起動を跨いで baseline が保持される。"""
    tmp = Path(tempfile.mkdtemp()) / "emotion_state.json"
    state = EmotionState(baselines={"喜び": 0.15})
    state.baseline["喜び"] = 0.42
    state.baseline["信頼"] = 0.31
    save_emotion_from_state(tmp, state)

    restored = EmotionState(baselines={"喜び": 0.15, "信頼": 0.2})
    apply_loaded_to_emotion(restored, load_emotion_state(tmp))
    assert restored.baseline["喜び"] == 0.42
    assert restored.baseline["信頼"] == 0.31
