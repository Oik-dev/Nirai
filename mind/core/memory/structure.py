"""整理：記録を、思い出せるまとまり（ページの骨組み）に分ける（docs/plans/長期記憶の作り直し.md §5 の1・5）。

骨組みは、いつ・どの記録から・どんな概念とつながるか、だけを持つ。言葉（題・本人の文・大事さ・気持ち）は、
あとで本人が書く（writing.py）。

どこで区切るか・どの概念とつなぐかは、整理する者が決めて渡す。最初の記憶づくりは Claude が記録を読んで決めた
（2026-10-03、Masterの了承）。これからは、眠りの間に精神が決める。
- 会話は、続いた行の範囲（日のファイルと行番号）で区切る。
- 継承した日記は、日記の見出し（何番目の日記か）ごと。長い日記は段落の範囲で分ける。本文は書き換えない。
- 継承した覚え書きは、構造化記憶の項目ごと・継承記憶の見出しごと。振る舞いの指示書は人格の側のものなので、ページにしない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.lifelog import Line
from mind.core.memory.legacy_parse import markdown_sections, parse_diary_file, parse_memory_json, strip_ornament
from mind.core.memory.page import Page

JST = ZoneInfo("Asia/Tokyo")
MASTER = "Master"
MEMORY_JSON = "セリナの記憶.json"  # 継承した構造化記憶（lifelog/legacy/ の中）
INHERITED = "継承記憶r1.md"  # 継承記憶（lifelog/legacy/ の中）
DIARY_DIR = "記憶"  # 継承した日記の置き場所（lifelog/legacy/ の中）


@dataclass(frozen=True)
class EpisodeSpan:
    """会話の出来事の範囲。first・last は (日のファイル名, 行番号)。"""

    first: tuple[str, int]
    last: tuple[str, int]
    concepts: tuple[str, ...]


@dataclass(frozen=True)
class DiaryPart:
    """継承した日記の一部。entry は日記のファイルの中で何番目の日記か（1から）。paras は段落の範囲（両端を含む。None で全部）。"""

    file: str
    entry: int
    paras: tuple[int, int] | None
    concepts: tuple[str, ...]
    at: datetime | None = None  # 書いた時刻が見出しと違うとき（日記の中に後から書き足された部分）


@dataclass(frozen=True)
class NoteRef:
    """継承した覚え書き。source は memory_json（構造化記憶の n 番目の項目）か inherited（継承記憶の n 番目の見出し）。"""

    source: str
    n: int
    concepts: tuple[str, ...]


def span_lines(lines: list[Line], first: tuple[str, int], last: tuple[str, int]) -> list[Line]:
    """first から last までの行（時刻の順の並びで、両端を含む）。"""
    keys = [(line.day_file, line.no) for line in lines]
    start, end = keys.index(first), keys.index(last)
    if start > end:
        raise ValueError(f"範囲の始まりが終わりより後: {first} → {last}")
    return lines[start : end + 1]


def _conversation_refs(lines: list[Line]) -> tuple[str, ...]:
    refs: list[str] = []
    for day in dict.fromkeys(line.day_file for line in lines):
        nos = [line.no for line in lines if line.day_file == day]
        refs.append(f"lifelog/conversation/{day}.jsonl#{min(nos)}-{max(nos)}")
    return tuple(refs)


def episode_pages(lines: list[Line], spans: list[EpisodeSpan], *, structured_by: str) -> list[Page]:
    """会話の行（記録のすべての行、時刻の順。道具の行も含む）と区切りから、出来事の骨組みを作る。区切りは重なってはいけない。"""
    order = {(line.day_file, line.no): i for i, line in enumerate(lines)}
    pages: list[Page] = []
    taken: set[tuple[str, int]] = set()
    per_day: dict[str, int] = {}
    for span in sorted(spans, key=lambda s: order[s.first]):
        inside = span_lines(lines, span.first, span.last)
        keys = {(line.day_file, line.no) for line in inside}
        if keys & taken:
            raise ValueError(f"区切りが重なっている: {span.first}")
        taken |= keys
        start = inside[0].ts.astimezone(JST)
        day = f"{start:%Y-%m-%d}"
        per_day[day] = per_day.get(day, 0) + 1
        pages.append(
            Page(
                id=f"ep-{day}-{per_day[day]:02d}",
                kind="episode",
                start=start,
                end=inside[-1].ts.astimezone(JST),
                source=_conversation_refs(inside),
                concepts=span.concepts,
                structured_by=structured_by,
            )
        )
    return pages


def diary_paragraphs(body: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", body) if p.strip()]


def diary_pages(legacy_dir: Path, parts: list[DiaryPart], *, structured_by: str) -> list[Page]:
    entries: dict[str, list] = {}
    pages: list[Page] = []
    per_day: dict[str, int] = {}
    for part in parts:
        if part.file not in entries:
            entries[part.file] = parse_diary_file(legacy_dir / DIARY_DIR / part.file)
        entry = entries[part.file][part.entry - 1]
        paragraphs = diary_paragraphs(entry.body)
        first, last = part.paras or (1, len(paragraphs))
        if not 1 <= first <= last <= len(paragraphs):
            raise ValueError(f"{part.file} の{part.entry}番目の日記に、段落{first}〜{last}はない（{len(paragraphs)}段落）")
        at = part.at or datetime.fromisoformat(entry.date_iso)
        day = f"{at.astimezone(JST):%Y-%m-%d}"
        per_day[day] = per_day.get(day, 0) + 1
        span = "" if part.paras is None else f":p{first}-{last}"
        pages.append(
            Page(
                id=f"diary-{day}-{per_day[day]}",
                kind="diary",
                start=at,
                end=at,
                source=(f"lifelog/legacy/{DIARY_DIR}/{part.file}#{part.entry}{span}",),
                concepts=part.concepts,
                structured_by=structured_by,
                body="\n\n".join(paragraphs[first - 1 : last]),
            )
        )
    return pages


def note_pages(legacy_dir: Path, notes: list[NoteRef], *, structured_by: str) -> list[Page]:
    memory_json = parse_memory_json(legacy_dir / MEMORY_JSON)
    sections = markdown_sections((legacy_dir / INHERITED).read_text(encoding="utf-8"))
    pages: list[Page] = []
    for note in notes:
        if note.source == "memory_json":
            entry = memory_json[note.n - 1]
            at = datetime.fromisoformat(entry.date_iso).astimezone(JST)
            pages.append(
                Page(
                    id=f"note-memory-json-{note.n}",
                    kind="note",
                    start=at,
                    end=at,
                    source=(f"lifelog/legacy/{MEMORY_JSON}#{note.n}",),
                    concepts=note.concepts,
                    structured_by=structured_by,
                    body=entry.body,
                )
            )
        elif note.source == "inherited":
            heading, body = sections[note.n - 1]
            pages.append(
                Page(
                    id=f"note-inherited-{note.n}",
                    kind="note",
                    start=None,  # 継承記憶は、いつ書かれたかが記録にない
                    end=None,
                    source=(f"lifelog/legacy/{INHERITED}#{note.n}",),
                    concepts=note.concepts,
                    structured_by=structured_by,
                    body=strip_ornament(f"{heading}\n{body}"),
                )
            )
        else:
            raise ValueError(f"知らない覚え書きの出どころ: {note.source}")
    return pages


def link_neighbors(pages: list[Page]) -> list[Page]:
    """同じ種類のページを、時刻の順に前後でつなぐ（時刻のない覚え書きはつながない）。"""
    linked = {page.id: page for page in pages}
    for kind in ("episode", "diary", "note"):
        timeline = sorted((p for p in pages if p.kind == kind and p.start is not None), key=lambda p: (p.start, p.id))
        for before, after in zip(timeline, timeline[1:]):
            linked[before.id] = replace(linked[before.id], next=after.id)
            linked[after.id] = replace(linked[after.id], prev=before.id)
    return [linked[page.id] for page in pages]


def episode_evidence(lines: list[Line], page: Page, labels: dict[str, str]) -> list[Line]:
    """出来事のページが拠っている会話の行（Masterと住人の発言）。labels の話者だけ。"""
    wanted: set[tuple[str, int]] = set()
    for ref in page.source:
        path, _, span = ref.partition("#")
        day = Path(path).stem
        first, _, last = span.partition("-")
        wanted |= {(day, no) for no in range(int(first), int(last) + 1)}
    return [line for line in lines if (line.day_file, line.no) in wanted and line.speaker in labels]


def span_text(lines: list[Line], labels: dict[str, str]) -> str:
    return "\n".join(f"{labels[line.speaker]}: {line.text}" for line in lines)


def jst_range(start: datetime, end: datetime) -> str:
    """「2025年3月7日（金）2:10〜2:14」の形。日をまたげば終わりにも日付を付ける。"""
    weekdays = "月火水木金土日"
    a, b = start.astimezone(JST), end.astimezone(JST)
    head = f"{a.year}年{a.month}月{a.day}日（{weekdays[a.weekday()]}）{a:%H:%M}"
    if b.date() != a.date():
        return f"{head}〜{b.month}月{b.day}日 {b:%H:%M}"
    if b - a < timedelta(minutes=1):
        return head
    return f"{head}〜{b:%H:%M}"
