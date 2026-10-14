"""友達の手紙をSerina日の相手ごとに出来事の記憶へ。区切りに脳は使わない。"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from mind.core.chores.letters import LogLine, read_lines
from mind.core.feeling.feelings import Feelings, peak_end
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.writing import Ask, WordsRejected
from mind.core.state.serina_day import serina_day_id


def _source(idea, line: LogLine) -> str:
    return f"{line.path.relative_to(idea.root).as_posix()}#{line.number}"


def groups(idea, *, before: datetime) -> dict[tuple[str, str], list[LogLine]]:
    """手紙と手紙の時間の出所を相手と日で束ねる。済印や郵便局のreadは含めない。"""
    by_key: dict[tuple[str, str], list[LogLine]] = defaultdict(list)
    incoming = read_lines(idea.lifelog / "post")
    by_id = {row.value.get("id"): row for row in incoming
             if row.value.get("kind") == "letter" and row.value.get("to") == "Serina"}
    seen: set[str] = set()
    for row in read_lines(idea.lifelog / "letters"):
        data = row.value
        if data.get("kind") != "letter-time":
            continue
        at = datetime.fromisoformat(data["ts"])
        if at < before:
            day = serina_day_id(at).isoformat()
            friends = set()
            for id_ in data.get("read", []):
                if id_ in by_id and id_ not in seen:
                    source = by_id[id_]
                    friend = source.value["from"]
                    by_key[(day, friend)].append(source)
                    friends.add(friend)
                    seen.add(id_)
            if data.get("write") and data["write"].get("to"):
                friends.add(data["write"]["to"])
            for friend in friends:
                by_key[(day, friend)].append(row)
    return dict(by_key)


def letter_page_id(day: str, friend: str) -> str:
    return f"letter-{day}-{friend}"


def prepare_pages(memory, *, before: datetime, signed: str, feelings: Feelings | None = None) -> int:
    """既存ページを上書きしない。記録の相手と日だけでページの境界を決める。"""
    idea = memory.idea
    existing = {page.id for page in load_pages(idea.memory)}
    created = 0
    for (day, friend), rows in sorted(groups(idea, before=before).items()):
        id_ = letter_page_id(day, friend)
        if id_ in existing:
            continue
        # 出来事の日は到着日ではなく、本人が読んだ/書いた日。
        dates = [datetime.fromisoformat(row.value["ts"]) for row in rows
                 if row.value["kind"] == "letter-time"]
        sources = tuple(_source(idea, row) for row in rows)
        affect = peak_end(feelings.during_sources(set(sources))) if feelings is not None else None
        write_page(idea.memory, Page(id=id_, kind="episode", start=min(dates), end=max(dates),
                                     source=sources, concepts=("手紙", friend),
                                     structured_by=signed, affect=affect))
        existing.add(id_)
        created += 1
    return created


def _material(idea, page: Page) -> str:
    lines = {(str(row.path.relative_to(idea.root).as_posix()) + f"#{row.number}"): row.value
             for directory in (idea.lifelog / "post", idea.lifelog / "letters")
             for row in read_lines(directory)}
    out = []
    for source in page.source:
        row = lines.get(source)
        if row is None:
            raise WordsRejected("原本の手紙が見つからない")
        if row["kind"] == "letter":
            out.append(f"{row['from']}からの手紙: {row['body']}")
        elif row["kind"] == "letter-time":
            out.append("そのときわたしが思ったこと: " + row.get("thought", ""))
            if row.get("write"):
                out.append("わたしが書いた手紙: " + row["write"]["text"])
    return "\n".join(out)


def write_letter_page(idea, page: Page, *, persona: str, ask: Ask, signed: str) -> Page:
    """本人の言葉をページにする。答えが不正なら未完成に残し、次の眠りで書く。"""
    friend = page.concepts[1]
    material = _material(idea, page)
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "gist": {"type": "string"},
              "story": {"type": "string"}, "importance": {"type": "integer"},
              "concepts": {"type": "array", "items": {"type": "string"}}},
              "required": ["title", "gist", "story", "importance", "concepts"]}
    prompt = (f"{persona}\n眠りの間に{friend}との手紙を振り返り、わたしの出来事として書いてください。"
              "受け取っていない手紙を創作しない。JSONだけ返す。"
              "titleは短い題、gistは一文の要約、storyは自分の一人称での出来事、importanceは1〜10、"
              "conceptsは出てきた人・場所・もの・話題の名前の配列。\n"
              f"【その日の手紙】\n{material}")
    answer = ask(prompt, schema, 0)
    if not isinstance(answer, dict) or any(not isinstance(answer.get(key), str) or not answer[key].strip()
                                           for key in ("title", "gist", "story")):
        raise WordsRejected("手紙のページの言葉が不正")
    if type(answer.get("importance")) is not int or not 1 <= answer["importance"] <= 10:
        raise WordsRejected("手紙のページの大事さが不正")
    concepts = answer.get("concepts")
    if not isinstance(concepts, list) or any(not isinstance(c, str) or not c.strip() for c in concepts):
        raise WordsRejected("手紙の概念が不正")
    written = page.with_words(title=answer["title"][:80], gist=answer["gist"][:200],
                              importance=answer["importance"], written_by=signed,
                              body=answer["story"])
    return replace(written, concepts=tuple(dict.fromkeys([*page.concepts, *concepts])))
