"""記録を読み、問題の材料になる単位（ひとつの出来事くらいのまとまり）に分ける。

記録はイデアの lifelog/（読み方は lifelog/README.md）。会話は、セッションと30分以上の間で区切り、
長すぎれば24発言か3000字で切る。継承した原本は、日記は日付ごと、構造化記憶は項目ごと、継承記憶は見出しごと。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.memory.legacy_parse import parse_diary_file, parse_memory_json, strip_ornament

JST = ZoneInfo("Asia/Tokyo")
SPEAKERS = ("Master", "Serina")
WINDOW_GAP = timedelta(minutes=30)
WINDOW_MAX = 24
WINDOW_CHARS = 3000
WINDOW_MIN = 6
SECTION_MIN_CHARS = 30

# 単位の出どころ。結果は出どころごとにも数える（どこが弱いかを見るため）
SOURCES = {
    "diary": "日記",
    "memory_json": "構造化記憶",
    "inherited": "継承記憶",
    "first_chat": "最初の会話",
    "local_chat": "ローカルの会話",
}


@dataclass(frozen=True)
class Line:
    """会話の1発言。no はその日のファイルの行番号（1から）。"""

    day_file: str
    no: int
    ts: datetime
    session: str
    speaker: str
    text: str


@dataclass(frozen=True)
class Unit:
    id: str
    source: str
    day: date | None
    text: str


def normalize(text: str) -> str:
    """目印を照らし合わせるための形。装飾・空白を落とし、全角半角をそろえる。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", strip_ornament(text)))


def read_conversation(lifelog: Path) -> list[Line]:
    lines: list[Line] = []
    for path in sorted((lifelog / "conversation").glob("*.jsonl")):
        with path.open(encoding="utf-8") as f:
            for no, raw in enumerate(f, start=1):
                if not raw.strip():
                    continue
                row = json.loads(raw)
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
    return lines


def conversation_source(session: str) -> str:
    return "first_chat" if session.startswith("chatgpt-") else "local_chat"


def _windows(lines: list[Line]) -> list[list[Line]]:
    windows: list[list[Line]] = []
    for line in lines:
        if line.speaker not in SPEAKERS:
            continue
        last = windows[-1][-1] if windows else None
        if (
            last is None
            or line.session != last.session
            or line.ts - last.ts > WINDOW_GAP
            or len(windows[-1]) >= WINDOW_MAX
            or sum(len(prev.text) for prev in windows[-1]) + len(line.text) > WINDOW_CHARS
        ):
            windows.append([line])
        else:
            windows[-1].append(line)
    merged: list[list[Line]] = []
    for window in windows:
        prev = merged[-1] if merged else None
        if prev and len(window) < WINDOW_MIN and window[0].session == prev[-1].session:
            prev.extend(window)
        else:
            merged.append(window)
    return merged


def _conversation_units(lines: list[Line]) -> list[Unit]:
    units = []
    for window in _windows(lines):
        first, last = window[0], window[-1]
        span = f"{first.day_file}#{first.no}" + (f"-{last.day_file}#{last.no}" if last.day_file != first.day_file else f"-{last.no}")
        units.append(
            Unit(
                id=f"conversation:{span}",
                source=conversation_source(first.session),
                day=first.ts.astimezone(JST).date(),
                text="\n".join(f"{line.speaker}: {line.text}" for line in window),
            )
        )
    return units


def _day(iso: str) -> date:
    return datetime.fromisoformat(iso).date()


def _sections(markdown: str) -> list[tuple[str, str]]:
    """見出し（#〜###）ごとに、見出しと本文を返す。小見出しには、上の見出しを「›」でつなぐ。

    「マスターの特徴」の下の「基本情報」を、Serina自身の基本情報と取り違えないため。
    """
    parts = re.split(r"^(#{1,3}\s+.*)$", markdown, flags=re.MULTILINE)
    out = []
    parents: dict[int, str] = {}
    for i in range(1, len(parts) - 1, 2):
        level = len(parts[i]) - len(parts[i].lstrip("#"))
        title = parts[i].lstrip("#").strip()
        parents = {lv: t for lv, t in parents.items() if lv < level} | {level: title}
        path = [parents[lv] for lv in sorted(parents) if lv > 1]  # 1段目は文書の題
        out.append((" › ".join(path) or title, parts[i + 1]))
    return out


def read_units(lifelog: Path) -> list[Unit]:
    units = _conversation_units(read_conversation(lifelog))
    legacy = lifelog / "legacy"
    seen: dict[str, int] = {}  # 同じ日の日記が別のファイルにもある
    for path in sorted((legacy / "記憶").glob("セリナの日記*.txt")):
        for entry in parse_diary_file(path):
            day = _day(entry.date_iso)
            seen[day.isoformat()] = seen.get(day.isoformat(), 0) + 1
            suffix = "" if seen[day.isoformat()] == 1 else f"-{seen[day.isoformat()]}"
            units.append(Unit(id=f"diary:{day.isoformat()}{suffix}", source="diary", day=day, text=entry.body))
    memory_json = legacy / "セリナの記憶.json"
    if memory_json.exists():
        for n, entry in enumerate(parse_memory_json(memory_json), start=1):
            units.append(Unit(id=f"memory_json:{n}", source="memory_json", day=_day(entry.date_iso), text=entry.body))
    inherited = legacy / "継承記憶r1.md"
    if inherited.exists():
        for n, (title, body) in enumerate(_sections(inherited.read_text(encoding="utf-8")), start=1):
            text = strip_ornament(f"{title}\n{body}")
            if len(normalize(body)) >= SECTION_MIN_CHARS:
                units.append(Unit(id=f"inherited:{n}", source="inherited", day=None, text=text))
    return units
