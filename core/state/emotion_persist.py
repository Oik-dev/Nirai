"""感情状態の永続化。episodic_state.py と同型（JSON・UTF-8・tmp+os.replace）。

`EmotionState`(core/state/emotion.py)はI/Oを持たせない方針のため、
アプリ/Core境界のこのモジュールが担う。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from serina.core.state.emotion import PLUTCHIK_AXES, EmotionState

DEFAULT_EMOTION_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "emotion_state.json"
)


def load_emotion_state(path: Path | str = DEFAULT_EMOTION_STATE_PATH) -> dict:
    """affect, mood, last_tick_at(iso or None) を読み込む。ファイルが無ければ初期値。"""
    target = Path(path)
    if not target.exists():
        return {
            "affect": {axis: 0.0 for axis in PLUTCHIK_AXES},
            "mood": {axis: 0.0 for axis in PLUTCHIK_AXES},
            "last_tick_at": None,
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "affect": data.get("affect", {axis: 0.0 for axis in PLUTCHIK_AXES}),
        "mood": data.get("mood", {axis: 0.0 for axis in PLUTCHIK_AXES}),
        "last_tick_at": data.get("last_tick_at"),
    }


def save_emotion_state(
    path: Path | str,
    *,
    affect: dict[str, float],
    mood: dict[str, float],
    last_tick_at: datetime | None,
) -> None:
    """アトミック保存（一時ファイル+os.replace。diary_state.py と同じ理由）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "affect": affect,
        "mood": mood,
        "last_tick_at": last_tick_at.isoformat() if last_tick_at is not None else None,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def apply_loaded_to_emotion(emotion: EmotionState, data: dict) -> None:
    """load_emotion_state の結果を EmotionState へ反映する。"""
    for axis in PLUTCHIK_AXES:
        emotion.affect[axis] = float(data.get("affect", {}).get(axis, 0.0))
        emotion.mood[axis] = float(data.get("mood", {}).get(axis, 0.0))
    raw_tick = data.get("last_tick_at")
    emotion.last_tick_at = datetime.fromisoformat(raw_tick) if raw_tick else None


def snapshot_from_emotion(emotion: EmotionState) -> dict:
    """save 用の値を EmotionState から生成する（last_tick_at は datetime のまま）。"""
    return {
        "affect": dict(emotion.affect),
        "mood": dict(emotion.mood),
        "last_tick_at": emotion.last_tick_at,
    }


def save_emotion_from_state(path: Path | str, emotion: EmotionState) -> None:
    snap = snapshot_from_emotion(emotion)
    save_emotion_state(
        path,
        affect=snap["affect"],
        mood=snap["mood"],
        last_tick_at=snap["last_tick_at"],
    )
