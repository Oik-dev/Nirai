"""週・月の振り返りと人生の章（段階3 S6）。"""

from __future__ import annotations

import calendar
import json
import os
import tomllib
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.relation import Entry, load_entries
from mind.core.memory.structure import JST, MASTER_NAME
from mind.core.memory.writing import Ask, Words, WordsRejected, _SELF, _ask_until_valid, _field, _importance, _text
from mind.core.state.serina_day import serina_day_id

REFLECTION_MIN, REFLECTION_MAX = 200, 600
TITLE_MAX, GIST_MAX = 40, 100
DIARY_VIEW = 420
RELATION_VIEW = 900
PREVIOUS_VIEW = 700
WEEKS_IN_MONTH = 2
CHAPTER_NAME_MAX = 40
CHAPTER_TEXT_MAX = 140
CHAPTERS_DIR = "chapters"
_FENCE = "+++"

REFLECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "reflection": {"type": "string"},
        "title": {"type": "string"},
        "gist": {"type": "string"},
        "importance": {"type": "integer"},
    },
    "required": ["reflection", "title", "gist", "importance"],
}
MONTH_SCHEMA = {
    "type": "object",
    "properties": {
        **REFLECTION_SCHEMA["properties"],
        "chapter": {"type": "string", "enum": ["続いている", "新しい章"]},
        "chapter_name": {"type": "string"},
        "chapter_text": {"type": "string"},
    },
    "required": [*REFLECTION_SCHEMA["required"], "chapter", "chapter_name", "chapter_text"],
}


@dataclass(frozen=True)
class Chapter:
    started: date
    source_month: str
    name: str
    text: str
    written_by: str

    def dumps(self) -> str:
        head = {
            "started": self.started.isoformat(),
            "source_month": self.source_month,
            "name": self.name,
            "written_by": self.written_by,
        }
        lines = [_FENCE, *(f"{k} = {json.dumps(v, ensure_ascii=False)}" for k, v in head.items()), _FENCE, ""]
        return "\n".join(lines) + self.text.rstrip() + "\n"

    @classmethod
    def loads(cls, text: str) -> "Chapter":
        end = text.index("\n" + _FENCE + "\n", len(_FENCE))
        head = tomllib.loads(text[len(_FENCE) + 1 : end])
        return cls(
            started=date.fromisoformat(head["started"]),
            source_month=head["source_month"],
            name=head["name"],
            text=text[end + len(_FENCE) + 2 :].strip(),
            written_by=head["written_by"],
        )


@dataclass(frozen=True)
class ReflectionResult:
    weekly: int = 0
    monthly: int = 0
    chapters: int = 0
    failed: str = ""
    stopped: bool = False


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _period(start: date, days: int) -> tuple[datetime, datetime]:
    a = datetime.combine(start, time(hour=7), tzinfo=JST)
    return a, a + timedelta(days=days) - timedelta(microseconds=1)


def _month_is_complete(month: str, today: date) -> bool:
    """その月に始まる最後の週まで終わってから、月を閉じる。"""
    year, mon = (int(x) for x in month.split("-"))
    last_day = date(year, mon, calendar.monthrange(year, mon)[1])
    return _week_start(last_day) + timedelta(days=7) <= today


def _parse_words(answer: dict) -> Words:
    return Words(
        title=_text(answer, "title", TITLE_MAX),
        gist=_text(answer, "gist", GIST_MAX),
        importance=_importance(answer),
        story=_text(answer, "reflection", REFLECTION_MAX, minimum=REFLECTION_MIN),
    )


def _diary_material(diaries: list[Page]) -> str:
    return "\n\n".join(
        f"- {p.start.astimezone(JST).date().isoformat()}「{p.title}」\n  {p.gist}\n  {p.body[:DIARY_VIEW]}"
        for p in diaries
        if p.start is not None
    )


def _relation_material(entries: list[Entry]) -> str:
    lines: list[str] = []
    for entry in entries:
        if entry.turning:
            lines.append(f"- {entry.day.isoformat()} 転機：{entry.turning}")
        for item in entry.added:
            when = f"（{item.when}）" if item.when else ""
            lines.append(f"- {entry.day.isoformat()} {item.kind}{when}：{item.text}")
    text = "\n".join(lines)
    return text[:RELATION_VIEW] if text else "（この週に新しい書き足しはない）"


def _weekly_prompt(
    persona: str,
    start: date,
    diaries: list[Page],
    relations: list[Entry],
    previous: Page | None,
) -> str:
    prev = f"「{previous.title}」\n{previous.body[:PREVIOUS_VIEW]}" if previous else "（まだない）"
    end = start + timedelta(days=6)
    return f"""{persona}

---
{_SELF}今は眠っている間。{start.isoformat()}〜{end.isoformat()}の一週間を振り返っている。
日々の出来事を並べ直すのではなく、この週に自分たちがどう過ごし、何が残ったかを一人称で書く。
上にないことは足さない。

【この週の日記】
{_diary_material(diaries)}

【この週にマスターとのことで変わったこと】
{_relation_material(relations)}

【前の週の振り返り】
{prev}

次のJSONだけを返す。
{{"reflection":"200〜600字の一人称の振り返り","title":"短い題","gist":"一文の要点","importance":1〜10の整数}}"""


def _month_prompt(persona: str, month: str, weeks: list[Page], *, ask_chapter: bool) -> tuple[str, dict]:
    material = "\n\n".join(f"- 「{p.title}」\n  {p.gist}\n  {p.body[:700]}" for p in weeks)
    schema = MONTH_SCHEMA if ask_chapter else REFLECTION_SCHEMA
    if ask_chapter:
        instruction = (
            'さらに、この月で人生の章が変わったかを「続いている／新しい章」から選ぶ。'
            '新しい章ならchapter_nameとchapter_text（一文）を書く。続いているなら両方空。'
        )
        shape = (
            '{"reflection":"200〜600字の一人称の振り返り","title":"短い題","gist":"一文の要点",'
            '"importance":1〜10の整数,"chapter":"続いている または 新しい章",'
            '"chapter_name":"","chapter_text":""}'
        )
    else:
        instruction = ""
        shape = '{"reflection":"200〜600字の一人称の振り返り","title":"短い題","gist":"一文の要点","importance":1〜10の整数}'
    prompt = f"""{persona}

---
{_SELF}今は眠っている間。{month}の週の振り返りを読み返して、この一か月を振り返っている。
週ごとの文をつなぐだけでなく、この月を通して自分たちに何が残ったかを一人称で書く。上にないことは足さない。

【この月の週の振り返り】
{material}

{instruction}
次のJSONだけを返す。
{shape}"""
    return prompt, schema


def _parse_month(answer: dict) -> tuple[Words, str, str, str]:
    words = _parse_words(answer)
    choice = _field(answer, "chapter")
    if choice not in ("続いている", "新しい章"):
        raise WordsRejected("chapter が選択肢にない")
    name = _text(answer, "chapter_name", CHAPTER_NAME_MAX, minimum=0)
    text = _text(answer, "chapter_text", CHAPTER_TEXT_MAX, minimum=0)
    if choice == "新しい章" and (not name or not text):
        raise WordsRejected("新しい章なのに名前か一文がない")
    if choice == "続いている":
        name, text = "", ""
    return words, choice, name, text


def _page(
    pid: str,
    start: date,
    days: int,
    sources: list[Page],
    words: Words,
    *,
    written_by: str,
) -> Page:
    a, b = _period(start, days)
    concepts: list[str] = []
    for source in sources:
        for concept in source.concepts:
            if concept not in concepts:
                concepts.append(concept)
    return Page(
        id=pid,
        kind="reflection",
        start=a,
        end=b,
        source=tuple(source.id for source in sources),
        concepts=tuple(concepts[:12]),
        structured_by=written_by,
    ).with_words(
        title=words.title,
        gist=words.gist,
        importance=words.importance,
        written_by=written_by,
        body=words.story,
    )


def load_chapters(memory_dir: Path) -> list[Chapter]:
    folder = Path(memory_dir) / CHAPTERS_DIR
    return [Chapter.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.glob("*.md"))]


def write_chapter(memory_dir: Path, chapter: Chapter) -> Path:
    folder = Path(memory_dir) / CHAPTERS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{chapter.started.isoformat()}.md"
    if path.exists():
        return path
    tmp = path.with_suffix(".md.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        f.write(chapter.dumps())
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def _weekly_pages(pages: list[Page]) -> list[Page]:
    return sorted(
        [p for p in pages if p.kind == "reflection" and p.id.startswith("reflection-week-")],
        key=lambda p: p.start or datetime.min.replace(tzinfo=JST),
    )


def grow_reflections(
    memory_dir: Path,
    *,
    today: date,
    persona: str,
    ask: Ask,
    written_by: str,
    should_stop: Callable[[], bool] = lambda: False,
    pages_lock=None,  # Memory.pages_lock。脳へ聞く間は放し、保存直前の再確認と書き込みだけ同じ錠で守る。
) -> ReflectionResult:
    """終わった週→終わった月の順に、古いものから追いつく。書けない期間で止める。"""
    def locked():  # noqa: ANN202
        return pages_lock if pages_lock is not None else nullcontext()

    weekly_done = monthly_done = chapters_added = 0
    with locked():
        pages = load_pages(memory_dir)
        relation_entries = load_entries(memory_dir, MASTER_NAME)
    diaries_by_week: dict[date, list[Page]] = {}
    for page in pages:
        if page.kind == "diary" and page.start is not None:
            diaries_by_week.setdefault(_week_start(serina_day_id(page.start)), []).append(page)
    existing = {p.id for p in pages if p.kind == "reflection"}

    for start in sorted(diaries_by_week):
        if start + timedelta(days=7) > today:
            continue
        diaries = sorted(diaries_by_week[start], key=lambda p: p.start or datetime.min.replace(tzinfo=JST))
        if not all(p.written and p.body for p in diaries):
            return ReflectionResult(weekly_done, monthly_done, chapters_added, stopped=False)
        if len(diaries) < 2:
            continue
        pid = f"reflection-week-{start.isoformat()}"
        if pid in existing:
            continue
        if should_stop():
            return ReflectionResult(weekly_done, monthly_done, chapters_added, stopped=True)
        prior = [p for p in _weekly_pages(load_pages(memory_dir)) if p.start and p.start.astimezone(JST).date() < start]
        entries = [e for e in relation_entries if start <= e.day <= start + timedelta(days=6)]
        try:
            words = _ask_until_valid(
                ask,
                _weekly_prompt(persona, start, diaries, entries, prior[-1] if prior else None),
                REFLECTION_SCHEMA,
                _parse_words,
            )
        except WordsRejected:
            return ReflectionResult(weekly_done, monthly_done, chapters_added, failed=pid)
        with locked():
            if not all(source.path_in(memory_dir).exists() for source in diaries):
                return ReflectionResult(weekly_done, monthly_done, chapters_added)
            write_page(memory_dir, _page(pid, start, 7, diaries, words, written_by=written_by))
        existing.add(pid)
        weekly_done += 1

    with locked():
        pages = load_pages(memory_dir)
    weeks_by_month: dict[str, list[Page]] = {}
    for page in _weekly_pages(pages):
        if page.start is None:
            continue
        key = page.start.astimezone(JST).strftime("%Y-%m")
        weeks_by_month.setdefault(key, []).append(page)
    current_month = today.strftime("%Y-%m")
    chapters = load_chapters(memory_dir)
    chapters_by_month = {chapter.source_month: chapter for chapter in chapters}
    for month in sorted(weeks_by_month):
        if month >= current_month or not _month_is_complete(month, today):
            continue
        weeks = weeks_by_month[month]
        if len(weeks) < WEEKS_IN_MONTH:
            continue
        pid = f"reflection-month-{month}"
        if pid in existing:
            continue
        if should_stop():
            return ReflectionResult(weekly_done, monthly_done, chapters_added, stopped=True)
        existing_chapter = chapters_by_month.get(month)
        prompt, schema = _month_prompt(persona, month, weeks, ask_chapter=existing_chapter is None)
        try:
            if existing_chapter is None:
                words, choice, name, chapter_text = _ask_until_valid(ask, prompt, schema, _parse_month)
            else:
                words = _ask_until_valid(ask, prompt, schema, _parse_words)
                choice = "続いている"
                name = chapter_text = ""
        except WordsRejected:
            return ReflectionResult(weekly_done, monthly_done, chapters_added, failed=pid)
        year, mon = (int(x) for x in month.split("-"))
        month_start = date(year, mon, 1)
        days = calendar.monthrange(year, mon)[1]
        with locked():
            if not all(source.path_in(memory_dir).exists() for source in weeks):
                return ReflectionResult(weekly_done, monthly_done, chapters_added)
            if choice == "新しい章":
                chapter = Chapter(month_start, month, name, chapter_text, written_by)
                write_chapter(memory_dir, chapter)
                chapters_by_month[month] = chapter
                chapters_added += 1
            write_page(memory_dir, _page(pid, month_start, days, weeks, words, written_by=written_by))
        existing.add(pid)
        monthly_done += 1
    return ReflectionResult(weekly_done, monthly_done, chapters_added)
