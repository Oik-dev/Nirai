"""問題集。測るたびに、次の2つから組み立てる。

- Claudeが記録を読んで書いた問題（イデアの questions.jsonl）。住人ごとに一度、記録を読んで書く（2026-10-03、
  Masterの判断。Gemmaに作らせると、物差しがGemmaの目利き止まりになる）。
- 記録から型で作る問題：「時間」（記録のある日を、日付・昨日・月で尋ねる）と「実際の会話」（ローカルに来てからの
  Masterの発言を、その時刻とその直前の流れのまま再生する）。同じIDの問題がquestions.jsonlにあれば、そちらを使う
  （思い出すべき記憶がはっきりしている発言に、答えを付けたもの）。

材料にする記録は TEST_NOW まで。新しい会話が増えても問題集は変わらないので、記憶を替えても同じ問題で比べられる。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from mind.memory_test.cases import Case, CaseSet, Turn, load_cases
from mind.memory_test.record import JST, Line, Unit, conversation_source, normalize, read_conversation, read_units

TEST_NOW = datetime(2026, 10, 3, 21, 0, tzinfo=JST)  # 問題集の「今」
EVENING = time(21, 0)
RECENT_TURNS = 4


def _evening(day: date) -> str:
    return datetime.combine(day, EVENING, tzinfo=JST).isoformat()


def time_cases(units: list[Unit]) -> list[Case]:
    days = sorted({unit.day for unit in units if unit.day})
    cases = []
    for day in days:
        last_year = day.year < TEST_NOW.year
        cases.append(
            Case(
                id=f"time:{day}:date",
                kind="time",
                utterance=f"{'去年の' if last_year else ''}{day.month}月{day.day}日のこと、覚えてる？",
                now=TEST_NOW.isoformat(),
                period=(day.isoformat(), day.isoformat()),
                author="template",
            )
        )
        cases.append(
            Case(
                id=f"time:{day}:yesterday",
                kind="time",
                utterance="昨日話したこと、覚えてる？",
                now=_evening(day + timedelta(days=1)),
                period=(day.isoformat(), day.isoformat()),
                author="template",
            )
        )
    for year, month in sorted({(day.year, day.month) for day in days}):
        first = date(year, month, 1)
        last = date(year + month // 12, month % 12 + 1, 1) - timedelta(days=1)
        cases.append(
            Case(
                id=f"time:{year}-{month:02d}:month",
                kind="time",
                utterance=f"{'去年の' if year < TEST_NOW.year else ''}{month}月ごろに話したこと、何か覚えてる？",
                now=TEST_NOW.isoformat(),
                period=(first.isoformat(), last.isoformat()),
                author="template",
            )
        )
    return cases


def replay_cases(lines: list[Line]) -> list[Case]:
    """ローカルに来てからのMasterの発言を、そのときの時刻と直前の流れで。"""
    cases = []
    talk = [line for line in lines if line.speaker in ("Master", "Serina")]
    for i, line in enumerate(talk):
        if line.speaker != "Master" or conversation_source(line.session) != "local_chat":
            continue
        before = [prev for prev in talk[max(0, i - RECENT_TURNS) : i] if prev.session == line.session]
        cases.append(
            Case(
                id=f"replay:{line.day_file}#{line.no}",
                kind="replay",
                utterance=line.text,
                now=line.ts.isoformat(),
                recent=tuple(Turn(prev.speaker, prev.text) for prev in before),
                source="local_chat",
                author="replay",
            )
        )
    return cases


def assemble(idea: Path) -> list[Case]:
    lifelog = idea / "lifelog"
    units = read_units(lifelog, until=TEST_NOW)
    written = load_cases(CaseSet.of_idea(idea).questions)
    normalized = [normalize(unit.text) for unit in units]
    for case in written:
        for group in case.marks:
            if not any(normalize(mark) in text for mark in group for text in normalized):
                raise ValueError(f"{case.id} の目印 {group} が記録にない")
    by_id = {case.id: case for case in written}
    templates = time_cases(units) + replay_cases(read_conversation(lifelog, until=TEST_NOW))
    return [by_id.pop(case.id, case) for case in templates] + list(by_id.values())
