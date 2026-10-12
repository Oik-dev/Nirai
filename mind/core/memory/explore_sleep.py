"""Turn completed research into episodes, grouped by thread and Serina day."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import replace
from datetime import datetime

from mind.core.chores.explore import ExploreLine, read_explore
from mind.core.feeling.body import JST
from mind.core.feeling.feelings import Feelings, peak_end
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.writing import Ask, WordsRejected
from mind.core.state.serina_day import serina_day_id


def groups(idea, *, before: datetime) -> dict[tuple[str, str], list[ExploreLine]]:
    """Only research with an actual reading becomes a research episode."""
    rows = read_explore(idea.lifelog / "explore")
    starts = {line.value["ts"]: line for line in rows if line.value.get("kind") == "explore-time"}
    grouped: dict[tuple[str, str], list[ExploreLine]] = defaultdict(list)
    for line in rows:
        read = line.value
        if read.get("kind") != "explored" or read.get("failed"):
            continue
        start = starts.get(read.get("time"))
        if start is None:
            continue
        at = datetime.fromisoformat(read["time"])
        if at >= before:
            continue
        key = (serina_day_id(at.astimezone(JST)).isoformat(), start.value["thread"])
        for entry in (start, line):
            if entry not in grouped[key]:
                grouped[key].append(entry)
    return dict(grouped)


def page_id(day: str, thread: str) -> str:
    return f"explore-{day}-{hashlib.sha256(thread.encode('utf-8')).hexdigest()[:16]}"


def prepare_pages(memory, *, before: datetime, signed: str, feelings: Feelings | None = None) -> int:
    """Append-only sources allow a page to be recreated without asking to segment."""
    idea = memory.idea
    existing = {page.id for page in load_pages(idea.memory)}
    created = 0
    for (day, thread), rows in sorted(groups(idea, before=before).items()):
        identifier = page_id(day, thread)
        if identifier in existing:
            continue
        stamps = [datetime.fromisoformat(row.value.get("ts") or row.value["time"]) for row in rows]
        sources = tuple(row.ref for row in rows)
        affect = peak_end(feelings.during_sources(set(sources))) if feelings is not None else None
        write_page(idea.memory, Page(id=identifier, kind="episode", start=min(stamps), end=max(stamps),
                                     source=sources, concepts=("調べもの",), structured_by=signed,
                                     affect=affect))
        existing.add(identifier)
        created += 1
    return created


def write_explore_page(idea, page: Page, *, persona: str, ask: Ask, signed: str) -> Page:
    """Never invent results: the prompt includes only traceable source rows."""
    by_ref = {row.ref: row.value for row in read_explore(idea.lifelog / "explore")}
    text = []
    for source in page.source:
        row = by_ref.get(source)
        if row is None:
            raise WordsRejected("調べものの原本が見つからない")
        if row["kind"] == "explore-time":
            text.append(f"問い: {row['question']}")
        elif row["kind"] == "explored":
            for result in row.get("results", []):
                text.append(f"読んだもの: {result['title']} / {result['url']} / {result['excerpt']}")
            text.append(f"そのときわたしが思ったこと: {row.get('thought', '')}")
            if row.get("next"):
                text.append(f"次に知りたいこと: {row['next']}")
    material = "\n".join(text)
    prompt = (f"{persona}\n眠りの間に、自分で調べたことを思い出して出来事のページに書いて。"
              "原本にない話を作らない。JSONだけ返す。"
              "titleは短い題、gistは一文、storyは自分の一人称、importanceは1〜10、"
              "conceptsは出てきた人・場所・もの・話題の名前の配列。\n"
              f"【そのときの記録】\n{material}")
    schema = {"type": "object", "properties": {
        "title": {"type": "string"}, "gist": {"type": "string"}, "story": {"type": "string"},
        "importance": {"type": "integer"}, "concepts": {"type": "array", "items": {"type": "string"}},
    }, "required": ["title", "gist", "story", "importance", "concepts"]}
    answer = ask(prompt, schema, 0)
    if not isinstance(answer, dict) or any(not isinstance(answer.get(key), str) or not answer[key].strip()
                                           for key in ("title", "gist", "story")):
        raise WordsRejected("調べもののページの言葉が不正")
    if type(answer.get("importance")) is not int or not 1 <= answer["importance"] <= 10:
        raise WordsRejected("調べもののページの大事さが不正")
    concepts = answer.get("concepts")
    if not isinstance(concepts, list) or any(not isinstance(c, str) or not c.strip() for c in concepts):
        raise WordsRejected("調べものの概念が不正")
    written = page.with_words(title=answer["title"][:80], gist=answer["gist"][:200],
                              importance=answer["importance"], written_by=signed,
                              body=answer["story"])
    return replace(written, concepts=tuple(dict.fromkeys([*page.concepts, *concepts])))
