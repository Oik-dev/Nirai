"""Serina 日界処理の永続状態。設計書 §2.4。

`last_boundary_serina_day` は電源断をまたいで保持する（再起動後の二重日界を防ぐ）。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

DEFAULT_SERINA_BOUNDARY_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "serina_boundary_state.json"
)


def load_serina_boundary_state(
    path: Path | str = DEFAULT_SERINA_BOUNDARY_STATE_PATH,
) -> date | None:
    target = Path(path)
    if not target.exists():
        return None
    data = json.loads(target.read_text(encoding="utf-8"))
    raw = data.get("last_boundary_serina_day")
    if not raw:
        return None
    return date.fromisoformat(raw)


def save_serina_boundary_state(
    path: Path | str,
    *,
    last_boundary_serina_day: date | None,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, str | None] = {
        "last_boundary_serina_day": (
            last_boundary_serina_day.isoformat() if last_boundary_serina_day else None
        ),
    }
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)
