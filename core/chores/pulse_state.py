"""Pulse 発火履歴の永続化（合意台帳 §3.6）。

約束ごと1回・同種3時間空け等の判定に使う。本番DB（memories）には触れない。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

DEFAULT_PULSE_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "pulse_state.json"
)


def load_pulse_state(path: Path | str = DEFAULT_PULSE_STATE_PATH) -> dict:
    target = Path(path)
    if not target.exists():
        return {
            "last_pulse_at": None,
            "last_by_kind": {},
            "pulsed_promise_ids": [],
        }
    data = json.loads(target.read_text(encoding="utf-8"))
    return {
        "last_pulse_at": data.get("last_pulse_at"),
        "last_by_kind": dict(data.get("last_by_kind") or {}),
        "pulsed_promise_ids": list(data.get("pulsed_promise_ids") or []),
    }


def save_pulse_state(
    path: Path | str,
    *,
    last_pulse_at: datetime | None,
    last_by_kind: dict[str, str],
    pulsed_promise_ids: list[int],
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_pulse_at": last_pulse_at.isoformat() if last_pulse_at else None,
        "last_by_kind": last_by_kind,
        "pulsed_promise_ids": pulsed_promise_ids,
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
    last_by_kind = dict(state.get("last_by_kind") or {})
    last_by_kind[kind] = fired_at.isoformat()
    pulsed = list(state.get("pulsed_promise_ids") or [])
    if kind == "memory" and trigger_id.isdigit():
        pid = int(trigger_id)
        if pid not in pulsed:
            pulsed.append(pid)
    return {
        "last_pulse_at": fired_at.isoformat(),
        "last_by_kind": last_by_kind,
        "pulsed_promise_ids": pulsed,
    }
