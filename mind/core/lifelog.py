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

本人から話しかけたことは lifelog/pulse/<日本時間の年月>.jsonl に追記する（いつ・種類・きっかけ）。
つながり Pulse の間隔は、この記録と会話の原文から返事の有無・速さを読み直して決める。

気持ちは lifelog/feeling/<日本時間の日付>.jsonl に追記する（Masterが話したターンごとに、本人が感じたことの言葉と評価と、
動いたあとの体の芯。core/feeling/）。今の気持ちは、この記録の最後の行から計算し直せる。

記録の場所は「lifelog/conversation/<日>.jsonl#始め-終わり」の形で書く（記憶のページと気持ちの記録が、拠った会話をこの形で持つ）。
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core import idea

MASTER = "Master"
CONVERSATION_SOURCE = "lifelog/conversation/"

_JST = ZoneInfo("Asia/Tokyo")
# 住人1人につき1プロセスなので、ファイルの追記と書き直しはこの1本の錠で順番にする。
_LOCK = threading.Lock()

Position = tuple[str, int]  # 会話の1行の場所（日のファイル名, 行番号）


def refs_of(positions: Iterable[Position]) -> tuple[str, ...]:
    """会話の行の場所を、記録の場所の書き方（日のファイルと、続いた行番号の範囲）にする。並びは日の順のまま。"""
    refs: list[str] = []
    positions = list(positions)
    for day in dict.fromkeys(day for day, _no in positions):
        nos = sorted(no for d, no in positions if d == day)
        first = prev = nos[0]
        for no in nos[1:] + [None]:
            if no is not None and no == prev + 1:
                prev = no
                continue
            refs.append(f"{CONVERSATION_SOURCE}{day}.jsonl#{first}-{prev}")
            if no is not None:
                first = prev = no
    return tuple(refs)


def positions_of(refs: Iterable[str]) -> set[Position]:
    """記録の場所の書き方から、会話の行の場所を取り出す。会話でない記録（継承した原本など）は含めない。"""
    out: set[Position] = set()
    for ref in refs:
        path, _, span = ref.partition("#")
        if not path.startswith(CONVERSATION_SOURCE):
            continue
        first, _, last = span.partition("-")
        out |= {(Path(path).stem, no) for no in range(int(first), int(last) + 1)}
    return out


def _ends_mid_line(path: Path) -> bool:
    """最後の行が改行なしで途切れているか（電源断などの書きかけ）。"""
    if not path.exists() or path.stat().st_size == 0:
        return False
    with path.open("rb") as f:
        f.seek(-1, os.SEEK_END)
        return f.read(1) != b"\n"


def _fsync_append(path: Path, row: dict) -> None:
    """1行を追記して、ディスクへ流してから戻る。書きかけで途切れた行があれば、先に改行で閉じる（くっつけない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "\n" if _ends_mid_line(path) else ""
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(head + json.dumps(row, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


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

    def _write(self, line: dict) -> Position:
        """1行を追記して、書いた場所を返す（行番号は、読むときと同じく空行も数える）。"""
        path = self._path(line["ts"])
        no = 1
        if path.exists():
            with path.open(encoding="utf-8") as f:
                no += sum(1 for _ in f)
        _fsync_append(path, line)
        return (path.stem, no)

    def append(self, *, ts: str, session: str, speaker: str, text: str) -> Position:
        """1発言を追記して、書いた場所（日のファイル名, 行番号）を返す。書き終えてディスクへ流してから戻る。"""
        with _LOCK:
            return self._write({"ts": ts, "session": session, "speaker": speaker, "text": text})

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

    def at(self, day_file: str, no: int) -> Line | None:
        """日のファイルと行番号で、消していない発言を1件だけ読む。"""
        if no < 1 or not day_file or any(ch not in "0123456789-" for ch in day_file):
            return None
        path = self.directory / f"{day_file}.jsonl"
        with _LOCK:
            rows = self._read_raw(path)
            if no > len(rows):
                return None
            row = rows[no - 1]
            if row is None or row.get("deleted"):
                return None
            return Line(
                day_file=day_file,
                no=no,
                ts=datetime.fromisoformat(row["ts"]),
                session=row["session"],
                speaker=row["speaker"],
                text=row["text"],
            )

    def remove_at(self, day_file: str, no: int) -> Line | None:
        """Masterが指定した1行だけを消した印へ置き換え、消す前の発言を返す。"""
        if no < 1 or not day_file or any(ch not in "0123456789-" for ch in day_file):
            return None
        path = self.directory / f"{day_file}.jsonl"
        with _LOCK:
            rows = self._read_raw(path)
            if no > len(rows):
                return None
            row = rows[no - 1]
            if row is None or row.get("deleted"):
                return None
            line = Line(
                day_file=day_file,
                no=no,
                ts=datetime.fromisoformat(row["ts"]),
                session=row["session"],
                speaker=row["speaker"],
                text=row["text"],
            )
            rows[no - 1] = {
                "ts": row["ts"], "session": row["session"], "speaker": row["speaker"], "deleted": True,
            }
            tmp = path.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                for item in rows:
                    f.write("\n" if item is None else json.dumps(item, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return line

    def remove(self, *, session: str, ts: str | None = None, speaker: str | None = None,
               text: str | None = None) -> list[Position]:
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
        erased: list[Position] = []
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
    intent（思い出そうとしていたか。眠りの再生だけ "replay"）。手がかりの原文は残さない（同じ時刻の会話の記録にある）。
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else idea.IDEA.recall

    def append(self, *, ts: datetime, page: str, activation: float, vivid: bool, intent: bool | str) -> None:
        path = self.directory / f"{ts.astimezone(_JST):%Y-%m}.jsonl"
        row = {"ts": ts.isoformat(), "page": page, "activation": round(activation, 3), "vivid": vivid, "intent": intent}
        with _LOCK:
            _fsync_append(path, row)

    def entries_on(self, day: date) -> list[dict]:
        """日本時間の1日に残った痕跡。途中で壊れた行は読み飛ばす。"""
        out: list[dict] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for raw in f:
                    if not raw.strip():
                        continue
                    try:
                        row = json.loads(raw)
                        if datetime.fromisoformat(row["ts"]).astimezone(_JST).date() == day:
                            out.append(row)
                    except (json.JSONDecodeError, KeyError, ValueError):
                        continue
        return out

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


class PulseLog:
    """本人から話しかけた記録（lifelog/pulse/<日本時間の年月>.jsonl）。

    1行＝1回の Pulse。いつ・種類・きっかけだけを追記する。
    S7 はこの記録と会話の生ログを突き合わせて、話しかけたあとに返事があったかを経験として読む。
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else idea.IDEA.pulse

    def append(self, *, ts: datetime, kind: str, trigger_id: str) -> None:
        path = self.directory / f"{ts.astimezone(_JST):%Y-%m}.jsonl"
        row = {"ts": ts.isoformat(), "kind": kind, "trigger_id": trigger_id}
        with _LOCK:
            _fsync_append(path, row)

    def entries(self) -> list[dict]:
        out: list[dict] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            with path.open(encoding="utf-8") as f:
                for raw in f:
                    if not raw.strip():
                        continue
                    try:
                        row = json.loads(raw)
                        datetime.fromisoformat(row["ts"])
                        if row.get("kind"):
                            out.append(row)
                    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
                        continue
        return sorted(out, key=lambda row: datetime.fromisoformat(row["ts"]))


class FeelingLog:
    """気持ちの記録（lifelog/feeling/<日本時間の日付>.jsonl）。1行＝Masterが話したターンに、本人が感じたこと。

    行：ts（UTC）・kind（turn。最初の1行だけ moved＝前の仕組みから移したもの）・source（拠った会話の場所）・feeling（本人の言葉）・evaluation（評価の選択肢と
    マスターの様子。評価を聞けなかったターンは null）・after（動いたあとの体の芯）。行の意味は core/feeling/ が決め、
    ここは追記と読み出しと、言葉を消すことだけをする。

    Masterが会話を消したら、その会話に拠った行の言葉（feeling・マスターの様子）だけを消して forgotten の印を付ける。
    評価の選択肢と体の芯の数は、そのときの心の動きとして残す（あとの気持ちは、この数から続いているため）。
    書きかけで壊れた行（電源断など）は読まない。
    """

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else idea.IDEA.feeling

    def append(self, row: dict) -> None:
        path = self.directory / f"{datetime.fromisoformat(row['ts']).astimezone(_JST).date().isoformat()}.jsonl"
        with _LOCK:
            _fsync_append(path, row)

    def _paths(self, days: Collection[str] | None = None) -> list[Path]:
        paths = sorted(self.directory.glob("*.jsonl"))
        return paths if days is None else [path for path in paths if path.stem in days]

    @staticmethod
    def _rows_in(path: Path) -> list[dict]:
        rows = []
        with path.open(encoding="utf-8") as f:
            for raw in f:
                if not raw.strip():
                    continue
                try:
                    rows.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        return rows

    def rows(self, days: Collection[str] | None = None) -> Iterator[dict]:
        """行を古い順に。days（日本時間の日付 "YYYY-MM-DD"）を渡せば、その日の行だけ。"""
        for path in self._paths(days):
            yield from self._rows_in(path)

    def newest_first(self) -> Iterator[dict]:
        """すべての行を、新しい順に（今の気持ちや直近の言葉は、新しい日のファイルから読むだけで済む）。"""
        for path in reversed(self._paths()):
            yield from reversed(self._rows_in(path))

    def forget(self, positions: Collection[Position]) -> int:
        """消された会話の行に拠っていた行の言葉を消す。言葉を消した行の数を返す。"""
        erased = set(positions)
        if not erased:
            return 0
        forgotten = 0
        with _LOCK:
            for path in self._paths():
                with path.open(encoding="utf-8") as f:
                    raws = f.readlines()
                out, hit = [], False
                for raw in raws:
                    try:
                        row = json.loads(raw) if raw.strip() else None
                    except json.JSONDecodeError:
                        row = None
                    if row is not None and positions_of(row.get("source", ())) & erased and not row.get("forgotten"):
                        row["feeling"] = ""
                        if isinstance(row.get("evaluation"), dict):
                            row["evaluation"]["master_state"] = ""
                        row["forgotten"] = True
                        raw = json.dumps(row, ensure_ascii=False) + "\n"
                        hit = True
                        forgotten += 1
                    out.append(raw)
                if not hit:
                    continue
                tmp = path.with_suffix(".jsonl.tmp")
                with tmp.open("w", encoding="utf-8", newline="\n") as f:
                    f.writelines(out)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
        return forgotten
