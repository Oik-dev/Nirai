"""調査用デバッグログ（AI 手渡し向け）。

GUI・チャットには出さない。会話本文は載せない。
ライブラリの雑多な DEBUG ではなく、調査に効くイベントだけを1行JSONで追記する。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mind.core.idea import DATA_DIR

DEFAULT_DEBUG_LOG_PATH = DATA_DIR / "logs" / "debug.jsonl"

_lock = threading.Lock()
_path: Path = DEFAULT_DEBUG_LOG_PATH

# 1フィールドの上限（AI に渡すときのトークン節約）
_MAX_FIELD_CHARS = 160


def configure(path: Path | str | None = None) -> None:
    """出力先を差し替える（テスト用）。None で既定パスへ戻す。"""
    global _path
    _path = DEFAULT_DEBUG_LOG_PATH if path is None else Path(path)


def path() -> Path:
    return _path


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[: _MAX_FIELD_CHARS - 1] + "…"
    return value


def emit(*, kind: str, action: str, **fields: Any) -> None:
    """調査イベントを1行追記する。失敗しても会話側へは伝播しない。"""
    entry: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "action": action,
    }
    for key, value in fields.items():
        if value is None:
            continue
        entry[key] = _clip(value)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with _lock:
            _path.parent.mkdir(parents=True, exist_ok=True)
            with _path.open("a", encoding="utf-8") as f:
                f.write(line)
    except OSError:
        # 調査ログの失敗で本処理を止めない
        pass


def read_recent(limit: int = 50) -> list[dict[str, Any]]:
    """末尾から最大 limit 件（新しい順ではない＝ファイル順の末尾）。"""
    if limit <= 0 or not _path.exists():
        return []
    lines = _path.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in lines[-limit:]:
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
