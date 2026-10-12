"""Research activity: append-only attempts, unanswered questions, and a fail-closed gate."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

from mind.core.feeling.body import JST
from mind.core.lifelog import _fsync_append
from mind.core.state.serina_day import serina_day_id


@dataclass(frozen=True)
class ExploreLine:
    ref: str
    value: dict


def read_explore(directory: Path) -> list[ExploreLine]:
    """Do not silently skip corrupt attempts: that could bypass the search quota."""
    lines = []
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as source:
            for no, raw in enumerate(source, 1):
                if not raw.strip():
                    raise ValueError(f"empty research row: {path}#{no}")
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError(f"invalid research row: {path}#{no}")
                lines.append(ExploreLine(f"lifelog/explore/{path.name}#{no}", row))
    return lines


def append_explore(directory: Path, row: dict) -> str:
    """Sync before network activity. The log line is the source reference."""
    at = datetime.fromisoformat(row.get("ts") or row["time"]).astimezone(JST)
    path = directory / f"{at:%Y-%m}.jsonl"
    _fsync_append(path, row)
    with path.open("rb") as stream:
        count = sum(1 for _ in stream)
    return f"lifelog/explore/{path.name}#{count}"


def attempts_today(lines: list[ExploreLine], at: datetime) -> int:
    day = serina_day_id(at.astimezone(JST))
    return sum(1 for line in lines if line.value.get("kind") == "explore-time"
               and line.value.get("kept") is None
               and serina_day_id(datetime.fromisoformat(line.value["ts"]).astimezone(JST)) == day)


def open_questions(lines: list[ExploreLine], *, limit: int = 3) -> list[dict]:
    """A follow-up remains open until an attempt names its original reading."""
    times = {line.value["ts"]: line.value for line in lines if line.value.get("kind") == "explore-time"}
    followed = {line.value.get("follows") for line in lines if line.value.get("kind") == "explore-time"}
    opened = []
    for line in reversed(lines):
        row = line.value
        if row.get("kind") != "explored" or not str(row.get("next") or "").strip():
            continue
        if row.get("time") in followed:
            continue
        origin = times.get(row.get("time"))
        if origin is None:
            continue
        opened.append({"time": row["time"], "question": row["next"],
                       "thread": origin["thread"], "brain": row.get("brain")})
        if len(opened) >= limit:
            break
    return opened


def explore_background(lines: list[ExploreLine], at: datetime, *, marks=None, cloud: bool = False) -> str:
    """Today's research and open threads, projected through the home marks for cloud."""
    day = serina_day_id(at.astimezone(JST))
    attempts = {line.value['ts']: line.value for line in lines if line.value.get('kind') == 'explore-time'}
    safe = (marks.safe_only if marks is not None else lambda text: '') if cloud else lambda text: text
    entries = []
    for line in reversed(lines):
        row = line.value
        start = attempts.get(row.get('time')) if row.get('kind') == 'explored' else None
        if start is None or serina_day_id(datetime.fromisoformat(row['time']).astimezone(JST)) != day:
            continue
        question, thought = safe(start['question']), safe(row.get('thought') or '')
        if question or thought:
            entries.append(f'- {question}：{thought}')
        if len(entries) == 3:
            break
    opened = [safe(item['question']) for item in open_questions(lines)]
    opened = [item for item in opened if item]
    if not entries and not opened:
        return ''
    return '\n'.join(['【今日の調べもの】', *reversed(entries),
                      *(['続けて知りたいこと：' + '、'.join(opened)] if opened else [])])


def research_trigger(lines: list[ExploreLine], *, activity: str | None, at: datetime,
                     selected_at: datetime | None, continuation_minutes: int = 30) -> dict | None:
    """Only a new choice or an unanswered reading can trigger work."""
    if activity != "調べものをする":
        return None
    times = [datetime.fromisoformat(line.value["ts"]) for line in lines
             if line.value.get("kind") == "explore-time"]
    last = max(times, default=None)
    if selected_at is not None and (last is None or selected_at > last):
        return {"kind": "chosen", "open": open_questions(lines)}
    readings = [line.value for line in lines if line.value.get("kind") == "explored"]
    if not readings:
        return None
    last_read = readings[-1]
    candidates = {row["time"]: row for row in open_questions(lines)}
    opened = candidates.get(last_read.get("time"))
    if opened and at - datetime.fromisoformat(last_read["time"]) >= timedelta(minutes=continuation_minutes):
        return {"kind": "follow", **opened}
    return None


def query_kept(question: str, brain_location: str, marks, judge: Callable[[list[str]], list[int]] | None) -> str | None:
    """Cloud-authored text is already outside; local-authored text must be marked."""
    if not question.strip():
        return "empty"
    if brain_location == "cloud":
        return None
    if marks is None or judge is None:
        return "unmarked"
    try:
        unknown = marks.unmarked([question])
        if unknown:
            marks.mark(unknown, judge(unknown))
        return "home" if marks.home(question) else None
    except Exception:
        return "judgement_failed"


def trimmed_results(results: list[dict], *, limit: int = 5, excerpt_chars: int = 400) -> list[dict]:
    return [{"title": str(row.get("title") or ""), "url": str(row.get("url") or ""),
             "excerpt": str(row.get("snippet") or row.get("excerpt") or "")[:excerpt_chars]}
            for row in results[:limit] if row.get("url")]


def mark_external_reading(marks, results: list[dict], *, cloud_words: tuple[str, ...] = ()) -> None:
    """Results from Tavily and text authored in the cloud have already been outside."""
    if marks is None:
        return
    parts = [str(value) for row in results for value in (row.get("title"), row.get("url"), row.get("excerpt"))
             if value]
    parts.extend(cloud_words)
    unknown = marks.unmarked(parts)
    if unknown:
        marks.mark(unknown, [])


def research_once(
    directory: Path, *, at: datetime, question: str, brain: str | None,
    brain_location: str, shown: dict | None, follows: str | None, thread: str | None,
    marks, judge: Callable[[list[str]], list[int]] | None, skill,
    read: Callable[[str, list[dict]], dict], daily_limit: int = 6,
) -> dict | None:
    """Persist intent before Tavily; never retry an attempt with unknown outcome."""
    if skill is None or not skill.enabled:
        return None
    lines = read_explore(directory)
    if attempts_today(lines, at) >= daily_limit:
        return None
    stamp = at.isoformat()
    kept = query_kept(question, brain_location, marks, judge)
    request = {"kind": "explore-time", "ts": stamp, "brain": brain,
               "thread": thread or stamp, "follows": follows, "shown": shown,
               "question": question, "kept": kept}
    attempt_ref = append_explore(directory, request)
    if kept is not None:
        return {"attempt": attempt_ref, "kept": kept}

    try:
        response = skill.search(question)
        if response is None:
            raise ValueError(skill.last_failure_reason or "search_failed")
        results = trimmed_results(response.results)
        mark_external_reading(marks, results)
    except Exception as exc:
        ref = append_explore(directory, {"kind": "explored", "time": stamp, "results": [],
                                         "failed": type(exc).__name__, "brain": None,
                                         "thought": "", "next": ""})
        return {"attempt": attempt_ref, "explored": ref, "failed": "search_failed"}

    try:
        answer = read(question, results)
        if not isinstance(answer, dict) or not isinstance(answer.get("thought"), str) or not isinstance(answer.get("next"), str):
            raise ValueError("invalid_reading")
    except Exception as exc:
        ref = append_explore(directory, {"kind": "explored", "time": stamp, "results": results,
                                         "failed": type(exc).__name__, "brain": None,
                                         "thought": "", "next": ""})
        return {"attempt": attempt_ref, "explored": ref, "failed": "read_failed"}
    ref = append_explore(directory, {"kind": "explored", "time": stamp, "results": results,
                                     "failed": None, "brain": brain,
                                     "thought": answer["thought"], "next": answer["next"]})
    mark_external_reading(marks, [], cloud_words=(answer["thought"], answer["next"])
                          if brain_location == "cloud" else ())
    return {"attempt": attempt_ref, "explored": ref, "answer": answer}
