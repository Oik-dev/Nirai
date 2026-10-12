"""起きるたび、初めて使えるようになった道を生ログへ追記する。"""

from __future__ import annotations

import json
import os
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from mind.core.idea import Idea

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'abilities.toml'


def rows(idea: Idea) -> list[dict]:
    path = idea.lifelog / 'abilities.jsonl'
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def record_new(idea: Idea, *, config: Path = CONFIG_PATH, now: datetime | None = None) -> list[str]:
    abilities = tomllib.loads(config.read_text(encoding='utf-8')).get('ability', {})
    known = {entry['id'] for entry in rows(idea)}
    pending = [(name, text) for name, text in abilities.items() if name not in known]
    if not pending:
        return []
    path = idea.lifelog / 'abilities.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    at = (now or datetime.now(timezone.utc)).isoformat()
    with path.open('a', encoding='utf-8') as f:
        for name, text in pending:
            f.write(json.dumps({'ts': at, 'kind': 'new_ability', 'id': name, 'text': text}, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
    return [name for name, _ in pending]
