"""感情状態の永続化（JSON・UTF-8・tmp+os.replace）。

`EmotionState`(core/state/emotion.py)はI/Oを持たせない方針のため、
アプリ/Core境界のこのモジュールが担う。情動・気分・平常値と、気分の流れ（Serina日ごとのスナップショット。
眠りの間に日記の材料にして片づける）を1つのファイルに持つ。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from mind.core.state.emotion import PLUTCHIK_AXES, EmotionState
from mind.core.idea import DATA_DIR

DEFAULT_EMOTION_STATE_PATH = DATA_DIR / "emotion_state.json"


def load_emotion_state(path: Path | str = DEFAULT_EMOTION_STATE_PATH) -> dict:
    """affect, mood, baseline, last_tick_at(iso or None) を読み込む。ファイルが無ければ初期値。"""
    target = Path(path)
    if not target.exists():
        # 2026-07-30 レビューI-b是正: baselineを辞書で返すとapply_loaded_to_emotion側の
        # isinstance(dict)判定が真になり、コンストラクタで注入したconfig初期値
        # （[emotion_baseline]）を新規環境の初回起動で全軸0.0に上書きしてしまう。
        # Noneにして「baselineキーが無い」旧ファイルと同じ経路（初期値を維持）へ揃える。
        return {
            "affect": {axis: 0.0 for axis in PLUTCHIK_AXES},
            "mood": {axis: 0.0 for axis in PLUTCHIK_AXES},
            "baseline": None,
            "last_tick_at": None,
            "mood_trajectory": [],
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "affect": data.get("affect", {axis: 0.0 for axis in PLUTCHIK_AXES}),
        "mood": data.get("mood", {axis: 0.0 for axis in PLUTCHIK_AXES}),
        "baseline": data.get("baseline"),
        "last_tick_at": data.get("last_tick_at"),
        "mood_trajectory": data.get("mood_trajectory", []),
    }


def save_emotion_state(
    path: Path | str,
    *,
    affect: dict[str, float],
    mood: dict[str, float],
    last_tick_at: datetime | None,
    baseline: dict[str, float] | None = None,
    mood_trajectory: list[dict] | None = None,
) -> None:
    """アトミック保存（一時ファイル+os.replace。途中で落ちても半端なファイルを残さない）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "affect": affect,
        "mood": mood,
        "last_tick_at": last_tick_at.isoformat() if last_tick_at is not None else None,
    }
    if baseline is not None:
        payload["baseline"] = baseline
    payload["mood_trajectory"] = mood_trajectory or []
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def apply_loaded_to_emotion(emotion: EmotionState, data: dict) -> None:
    """load_emotion_state の結果を EmotionState へ反映する。

    baseline キーが無い旧ファイルでは、コンストラクタで渡した初期値を維持する。
    """
    for axis in PLUTCHIK_AXES:
        emotion.affect[axis] = float(data.get("affect", {}).get(axis, 0.0))
        emotion.mood[axis] = float(data.get("mood", {}).get(axis, 0.0))
    loaded_baseline = data.get("baseline")
    if isinstance(loaded_baseline, dict):
        for axis in PLUTCHIK_AXES:
            if axis in loaded_baseline:
                emotion.baseline[axis] = float(loaded_baseline[axis])
    raw_tick = data.get("last_tick_at")
    emotion.last_tick_at = datetime.fromisoformat(raw_tick) if raw_tick else None
    emotion.mood_trajectory = list(data.get("mood_trajectory", []))


def snapshot_from_emotion(emotion: EmotionState) -> dict:
    """save 用の値を EmotionState から生成する（last_tick_at は datetime のまま）。"""
    return {
        "affect": dict(emotion.affect),
        "mood": dict(emotion.mood),
        "baseline": dict(emotion.baseline),
        "last_tick_at": emotion.last_tick_at,
        "mood_trajectory": list(emotion.mood_trajectory),
    }


def save_emotion_from_state(path: Path | str, emotion: EmotionState) -> None:
    snap = snapshot_from_emotion(emotion)
    save_emotion_state(
        path,
        affect=snap["affect"],
        mood=snap["mood"],
        baseline=snap["baseline"],
        last_tick_at=snap["last_tick_at"],
        mood_trajectory=snap["mood_trajectory"],
    )
