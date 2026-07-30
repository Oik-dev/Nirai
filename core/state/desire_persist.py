"""欲求状態の永続化。emotion_persist.py と同型（JSON・UTF-8・tmp+os.replace）。

`DesireState`(core/state/desire.py)はI/Oを持たせない方針のため、
アプリ/Core境界のこのモジュールが担う（Task 3-4 差し戻し解決）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from serina.core.state.desire import DesireState

DEFAULT_DESIRE_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "desire_state.json"
)


def load_desire_state(path: Path | str = DEFAULT_DESIRE_STATE_PATH) -> dict:
    """level, refractory_until(iso or None), last_tick_at(iso or None) を読む。

    ファイルが無ければ初期値。
    """
    target = Path(path)
    if not target.exists():
        return {
            "level": 0.0,
            "refractory_until": None,
            "last_tick_at": None,
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "level": data.get("level", 0.0),
        "refractory_until": data.get("refractory_until"),
        "last_tick_at": data.get("last_tick_at"),
    }


def save_desire_state(
    path: Path | str,
    *,
    level: float,
    refractory_until: datetime | None,
    last_tick_at: datetime | None,
) -> None:
    """アトミック保存（一時ファイル+os.replace。emotion_persist.pyと同じ理由）。"""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "level": level,
        "refractory_until": (
            refractory_until.isoformat() if refractory_until is not None else None
        ),
        "last_tick_at": last_tick_at.isoformat() if last_tick_at is not None else None,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def _parse_optional_datetime(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    # emotion/relationship と同様、naive は UTC として補う（aware now との減算事故防止）。
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def apply_loaded_to_desire(desire: DesireState, data: dict) -> None:
    """load_desire_state の結果を DesireState へ反映する。"""
    try:
        desire.level = float(data.get("level", 0.0))
    except (TypeError, ValueError):
        desire.level = 0.0
    desire.level = max(0.0, min(1.0, desire.level))
    # 2026-07-30: 起動直後の最初のtickより前でもgate.pyの判定
    # （desire.level_before_tick参照）が正しく動くよう、復元値で揃えておく。
    desire.level_before_tick = desire.level
    desire.refractory_until = _parse_optional_datetime(data.get("refractory_until"))
    desire.last_tick_at = _parse_optional_datetime(data.get("last_tick_at"))


def snapshot_from_desire(desire: DesireState) -> dict:
    """save 用の値を DesireState から生成する（日時は datetime のまま）。

    level_before_tick は意図的に含めない: apply_loaded_to_desire が復元時に
    level から導出するため、往復のたびに再構成すれば十分（2026-07-30）。
    """
    return {
        "level": desire.level,
        "refractory_until": desire.refractory_until,
        "last_tick_at": desire.last_tick_at,
    }


def save_desire_from_state(path: Path | str, desire: DesireState) -> None:
    snap = snapshot_from_desire(desire)
    save_desire_state(
        path,
        level=snap["level"],
        refractory_until=snap["refractory_until"],
        last_tick_at=snap["last_tick_at"],
    )
