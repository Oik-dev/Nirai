"""生ログ（イデアの lifelog/）。住人が経験したことの原文の正本。

長期記憶は、ここから作り直せる派生物になる（docs/plans/長期記憶の作り直し.md の原則1
「記録は全部・永遠に。記憶は人のように」）。脳や精神が替わっても、この原文から思い出せる。

会話は lifelog/conversation/<日本時間の日付>.jsonl に、1行1発言で追記する。行の形と、消してよいとき
（Masterが画面から明示的に消したときだけ）は、イデアの lifelog/README.md に書いてある。イデアは、
Niraiや精神がなくても読めるように、自分の記録の読み方を自分で持つ。
消した発言は、本文を消した印の行（deleted）として残す。記憶のページは記録を「日のファイルと行番号」で
指すので、行を詰めると、ほかの発言を指していたページが別の発言を指してしまうため。

思い出したことは lifelog/recall/<日本時間の年月>.jsonl に追記する（いつ・どのページを・どれだけの活性で）。
記憶の強さは、この記録から計算し直せる（core/memory/strength.py）。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core import idea

MASTER = "Master"

_JST = ZoneInfo("Asia/Tokyo")
# 住人1人につき1プロセスなので、ファイルの追記と書き直しはこの1本の錠で順番にする。
_LOCK = threading.Lock()


def _key(line: dict) -> tuple[str, str, str, str]:
    return (line["ts"], line["session"], line["speaker"], line["text"])


@dataclass(frozen=True)
class Line:
    """会話の1発言。no はその日のファイルの行番号（1から）。"""

    day_file: str
    no: int
    ts: datetime
    session: str
    speaker: str
    text: str


def read_conversation(directory: Path, *, until: datetime | None = None) -> list[Line]:
    """会話の全発言を時刻順に。until があれば、その時刻より前の発言だけ。消した発言は含めない（行番号は数える）。"""
    lines: list[Line] = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for no, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if row.get("deleted"):
                    continue
                lines.append(
                    Line(
                        day_file=path.stem,
                        no=no,
                        ts=datetime.fromisoformat(row["ts"]),
                        session=row["session"],
                        speaker=row["speaker"],
                        text=row["text"],
                    )
                )
    lines.sort(key=lambda line: line.ts)
    return [line for line in lines if until is None or line.ts < until]


class ConversationLog:
    """会話の生ログ。日本時間の日付ごとの JSON Lines。directory を省くと、このプロセスの住人の記録。"""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else idea.IDEA.conversation

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
                known = {_key(line) for line in self._read(path) if not line.get("deleted")}
                for line in sorted(candidates, key=lambda x: x["ts"]):
                    if _key(line) not in known:
                        self._write(line)
                        known.add(_key(line))
                        added += 1
        return added

    def remove(self, *, session: str, ts: str | None = None, speaker: str | None = None,
               text: str | None = None) -> list[tuple[str, int]]:
        """Masterが明示的に消した発言を、記録からも消す。消した行の (日のファイル名, 行番号) を返す。

        ts を渡せば、その発言（ts・話者・原文が一致する行）だけ。渡さなければセッションの全発言。
        本文を消して、消した印の行に置き換える（行番号は変えない）。
        ファイルは一時ファイルに書いてから置き換えるので、途中で落ちても半端に壊れない。
        """
        def matches(line: dict) -> bool:
            if line.get("deleted") or line["session"] != session:
                return False
            if ts is None:
                return True
            return line["ts"] == ts and line["speaker"] == speaker and line["text"] == text

        paths = [self._path(ts)] if ts is not None else sorted(self.directory.glob("*.jsonl"))
        erased: list[tuple[str, int]] = []
        with _LOCK:
            for path in paths:
                lines = self._read_raw(path)
                hit = [no for no, line in enumerate(lines, start=1) if line is not None and matches(line)]
                if not hit:
                    continue
                erased += [(path.stem, no) for no in hit]
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8", newline="\n") as f:
                    for no, line in enumerate(lines, start=1):
                        if no in hit:
                            line = {"ts": line["ts"], "session": line["session"], "speaker": line["speaker"], "deleted": True}
                        f.write("\n" if line is None else json.dumps(line, ensure_ascii=False) + "\n")
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
        return erased

    def _read_raw(self, path: Path) -> list[dict | None]:
        """行番号どおりの並び（空行は None）。書き直すときに行番号を保つため。"""
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as f:
            return [json.loads(raw) if raw.strip() else None for raw in f]


class RecallLog:
    """思い出したことの記録（lifelog/recall/<日本時間の年月>.jsonl）。1行＝1ページを思い出したこと。

    行：ts（思い出した時刻。UTC）・page（ページのid）・activation（そのときの活性）・vivid（はっきり思い出したか）・
    intent（思い出そうとしていたか）。手がかりの原文は残さない（同じ時刻の会話の記録にある）。
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else idea.IDEA.recall

    def append(self, *, ts: datetime, page: str, activation: float, vivid: bool, intent: bool) -> None:
        path = self.directory / f"{ts.astimezone(_JST):%Y-%m}.jsonl"
        row = {"ts": ts.isoformat(), "page": page, "activation": round(activation, 3), "vivid": vivid, "intent": intent}
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

    def times(self) -> dict[str, list[datetime]]:
        """ページごとの、思い出した時刻の並び。"""
        out: dict[str, list[datetime]] = {}
        for path in sorted(self.directory.glob("*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for raw in f:
                    if raw.strip():
                        row = json.loads(raw)
                        out.setdefault(row["page"], []).append(datetime.fromisoformat(row["ts"]))
        return out
