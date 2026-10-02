"""予定・記念日 Pulse 窓の発火済みフラグ（合意: 各窓は一度きり）。

`pulse_state.json` と同型の I/O（読み込み→dict操作→原子的書き込み）。
キーは `"{fact_id}:{window}"`（例 `"42:直前"`）、値は発火した ISO 時刻。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from mind.core.soul import DATA_DIR

DEFAULT_SCHEDULE_PULSE_STATE_PATH = DATA_DIR / "schedule_pulse_state.json"


def fired_key(fact_id: str, window: str) -> str:
    return f"{fact_id}:{window}"


def load_schedule_pulse_state(path: Path | str = DEFAULT_SCHEDULE_PULSE_STATE_PATH) -> dict:
    target = Path(path)
    if not target.exists():
        return {"fired": {}}
    data = json.loads(target.read_text(encoding="utf-8"))
    fired = data.get("fired")
    if not isinstance(fired, dict):
        fired = {}
    return {"fired": {str(k): str(v) for k, v in fired.items()}}


def save_schedule_pulse_state(
    path: Path | str,
    *,
    fired: dict[str, str],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"fired": dict(fired)}
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def is_window_fired(state: dict, fact_id: str, window: str) -> bool:
    fired = state.get("fired") or {}
    return fired_key(fact_id, window) in fired


def record_window_fire(
    state: dict,
    *,
    fact_id: str,
    window: str,
    fired_at: datetime,
) -> dict:
    """発火記録を更新した新しい state dict を返す（イミュータブル更新）。"""
    fired = dict(state.get("fired") or {})
    key = fired_key(fact_id, window)
    if key not in fired:
        fired[key] = fired_at.isoformat()
    return {"fired": fired}


def clear_fired_keys_for_fact(state: dict, fact_id: str) -> dict:
    """指定 fact_id に紐づく発火キーをすべて落とす。

    記念日の年次リセット、および予定の tombstone 時に使う。
    """
    prefix = f"{fact_id}:"
    fired = {
        k: v for k, v in dict(state.get("fired") or {}).items()
        if not str(k).startswith(prefix)
    }
    return {"fired": fired}
