"""Sleep 人格提案器の永続状態（最終提案試行日と、最後に読んだ振り返り）。

電源断をまたいでも「1日1回」と「同じ振り返りを何度も読まない」を守る。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from mind.core.idea import DATA_DIR

DEFAULT_PERSONA_PROPOSE_STATE_PATH = DATA_DIR / "persona_propose_state.json"

@dataclass(frozen=True)
class PersonaProposeState:
    last_propose_at: datetime | None = None
    after_reflection: str = ""


def load_persona_propose_state(
    path: Path | str = DEFAULT_PERSONA_PROPOSE_STATE_PATH,
) -> PersonaProposeState:
    """提案器の状態。旧いファイル（時刻だけ）も after_reflection="" として読める。"""
    target = Path(path)
    if not target.exists():
        return PersonaProposeState()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        raw = data.get("last_propose_at")
        return PersonaProposeState(
            last_propose_at=datetime.fromisoformat(raw) if raw else None,
            after_reflection=str(data.get("after_reflection", "")),
        )
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        return PersonaProposeState()


def save_persona_propose_state(
    path: Path | str,
    *,
    last_propose_at: datetime,
    after_reflection: str = "",
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if last_propose_at.tzinfo is None:
        last_propose_at = last_propose_at.replace(tzinfo=timezone.utc)
    payload = {"last_propose_at": last_propose_at.isoformat(), "after_reflection": after_reflection}
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)
