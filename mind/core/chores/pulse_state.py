"""Pulse 発火履歴の永続化（合意台帳 §3.6）。

同種ギャップ・最小間隔の判定に使う。本番DB（memories）には触れない。
予定窓の一度きり管理は `schedule_pulse_state.py` 側（Task 1-3）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from mind.core.idea import DATA_DIR

DEFAULT_PULSE_STATE_PATH = DATA_DIR / "pulse_state.json"


def load_pulse_state(path: Path | str = DEFAULT_PULSE_STATE_PATH) -> dict:
    target = Path(path)
    if not target.exists():
        return {
            "last_pulse_at": None,
            "last_by_kind": {},
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "last_pulse_at": data.get("last_pulse_at"),
        "last_by_kind": dict(data.get("last_by_kind") or {}),
    }


def save_pulse_state(
    path: Path | str,
    *,
    last_pulse_at: datetime | None,
    last_by_kind: dict[str, str],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_pulse_at": last_pulse_at.isoformat() if last_pulse_at else None,
        "last_by_kind": last_by_kind,
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)


def record_pulse_fire(
    state: dict,
    *,
    kind: str,
    trigger_id: str,
    fired_at: datetime,
) -> dict:
    """発火記録を更新した新しい state dict を返す（イミュータブル更新）。"""
    del trigger_id  # 予定窓の一度きりは schedule_pulse_state が担当
    last_by_kind = dict(state.get("last_by_kind") or {})
    last_by_kind[kind] = fired_at.isoformat()
    return {
        "last_pulse_at": fired_at.isoformat(),
        "last_by_kind": last_by_kind,
    }
