"""Sleep 人格提案器の永続状態（最終提案試行日）。

電源断をまたいでも「1日1回」を守るため、last_propose_at だけを JSON で持つ。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PERSONA_PROPOSE_STATE_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "persona_propose_state.json"
)


def load_persona_propose_state(
    path: Path | str = DEFAULT_PERSONA_PROPOSE_STATE_PATH,
) -> datetime | None:
    """最終提案試行時刻。ファイル無し・未記録は None（＝まだ今日は聞いていない）。"""
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        raw = data.get("last_propose_at")
        if not raw:
            return None
        return datetime.fromisoformat(raw)
    except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError):
        # 破損は「未記録」扱いで続行（起動を止めない）。最悪でも同日にもう一度聞くだけ。
        return None


def save_persona_propose_state(
    path: Path | str,
    *,
    last_propose_at: datetime,
) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if last_propose_at.tzinfo is None:
        last_propose_at = last_propose_at.replace(tzinfo=timezone.utc)
    payload = {"last_propose_at": last_propose_at.isoformat()}
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, target)
