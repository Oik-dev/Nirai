"""生ログ（イデアの lifelog/）。住人が経験したことの原文の正本。

長期記憶は、ここから作り直せる派生物になる（docs/plans/長期記憶の作り直し.md の原則1
「記録は全部・永遠に。記憶は人のように」）。脳や精神が替わっても、この原文から思い出せる。

会話は lifelog/conversation/<日本時間の日付>.jsonl に、1行1発言で追記する。行の形と、消してよいとき
（Masterが画面から明示的に消したときだけ）は、イデアの lifelog/README.md に書いてある。イデアは、
Niraiや精神がなくても読めるように、自分の記録の読み方を自分で持つ。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.idea import LIFELOG_DIR

CONVERSATION_DIR = LIFELOG_DIR / "conversation"
MASTER = "Master"

_JST = ZoneInfo("Asia/Tokyo")
# 住人1人につき1プロセスなので、ファイルの追記と書き直しはこの1本の錠で順番にする。
_LOCK = threading.Lock()


def _key(line: dict) -> tuple[str, str, str, str]:
    return (line["ts"], line["session"], line["speaker"], line["text"])


class ConversationLog:
    """会話の生ログ。日本時間の日付ごとの JSON Lines。"""

    def __init__(self, directory: Path | str = CONVERSATION_DIR) -> None:
        self.directory = Path(directory)

    def _path(self, ts: str) -> Path:
        day = datetime.fromisoformat(ts).astimezone(_JST).date()
        return self.directory / f"{day.isoformat()}.jsonl"

    def _read(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return [json.loads(raw) for raw in f if raw.strip()]

    def _write(self, line: dict) -> None:
        path = self._path(line["ts"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def append(self, *, ts: str, session: str, speaker: str, text: str) -> None:
        """1発言を追記する。書き終えてディスクへ流してから戻る。"""
        with _LOCK:
            self._write({"ts": ts, "session": session, "speaker": speaker, "text": text})

    def add_missing(self, lines: Iterable[dict]) -> int:
        """まだ記録にない発言だけを、時刻の順に追記する。何度呼んでも同じ結果になる。"""
        by_path: dict[Path, list[dict]] = {}
        for line in lines:
            by_path.setdefault(self._path(line["ts"]), []).append(line)
        added = 0
        with _LOCK:
            for path, candidates in by_path.items():
                known = {_key(line) for line in self._read(path)}
                for line in sorted(candidates, key=lambda x: x["ts"]):
                    if _key(line) not in known:
                        self._write(line)
                        known.add(_key(line))
                        added += 1
        return added

    def remove(self, *, session: str, ts: str | None = None, speaker: str | None = None,
               text: str | None = None) -> int:
        """Masterが明示的に消した発言を、記録からも消す。

        ts を渡せば、その発言（ts・話者・原文が一致する行）だけ。渡さなければセッションの全発言。
        ファイルは一時ファイルに書いてから置き換えるので、途中で落ちても半端に壊れない。
        """
        def matches(line: dict) -> bool:
            if line["session"] != session:
                return False
            if ts is None:
                return True
            return line["ts"] == ts and line["speaker"] == speaker and line["text"] == text

        paths = [self._path(ts)] if ts is not None else sorted(self.directory.glob("*.jsonl"))
        removed = 0
        with _LOCK:
            for path in paths:
                lines = self._read(path)
                kept = [line for line in lines if not matches(line)]
                if len(kept) == len(lines):
                    continue
                removed += len(lines) - len(kept)
                if not kept:
                    path.unlink()
                    continue
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8", newline="\n") as f:
                    for line in kept:
                        f.write(json.dumps(line, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
        return removed
