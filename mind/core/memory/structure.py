"""整理：記録を、思い出せるまとまり（ページの骨組み）に分ける（docs/plans/長期記憶の作り直し.md §5 の1・5）。

骨組みは、いつ・どの記録から・どんな概念とつながるか、だけを持つ。言葉（題・本人の文・大事さ・気持ち）は、
あとで本人が書く（writing.py）。

会話は、続いた行の範囲（日のファイルと行番号）で区切る。区切り方の原型は、最初の記憶づくりで Claude が記録を読んで
決めた形（2026-10-03、Masterの了承）：ひとつの話題や目的のまとまりを1つの出来事にする（話の流れが続くあいだは同じ出来事。
ふつう4〜30発言。最初の記憶では真ん中が10発言）。
これからは、眠りの間に本人の脳が区切る（segment）。セッションの切れ目と、30分以上の間はかならず区切り、
その中の話題の切れ目と、出来事につなぐ概念（人・場所・もの・話題の名前）は本人の脳が決める。
概念の名前は、呼び名の辞書（memory/concepts.toml）とこれまでのページの概念にそろえる。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mind.core.lifelog import Line, positions_of, refs_of
from mind.core.memory.page import Page

JST = ZoneInfo("Asia/Tokyo")
MASTER = "Master"  # 記録の話者
MASTER_NAME = "マスター"  # 住人から見たMasterの呼び名（記録を脳に見せるときと、関係の置き場所 memory/people/<呼び名>/）

SEGMENT_GAP = timedelta(minutes=30)  # これだけあいたら、かならず別の出来事
SEGMENT_WINDOW_CHARS = 3500  # 区切るときに脳へ一度に見せる記録の長さ（Gemmaの窓に、指示と一緒に収まる長さ）
SEGMENT_LINE_VIEW = 150  # 区切るときに見せる1行の長さ（頭だけ。話題の切れ目は頭で分かる）
MAX_CONCEPTS = 8
MIN_EPISODE_LINES = 4  # 出来事の大きさの下限（2往復）。これより短い区切りは前の出来事に含める（どこで区切るかは脳、大きさは仕組み）
CONCEPT_CHARS = 20

SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "episodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "integer"},
                    "concepts": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["first", "concepts"],
            },
        }
    },
    "required": ["episodes"],
}


class StructureRejected(ValueError):
    """脳の区切りが、ページの骨組みにできる形になっていない。"""


@dataclass(frozen=True)
class EpisodeSpan:
    """会話の出来事の範囲。first・last は (日のファイル名, 行番号)。"""

    first: tuple[str, int]
    last: tuple[str, int]
    concepts: tuple[str, ...]


def span_lines(lines: list[Line], first: tuple[str, int], last: tuple[str, int]) -> list[Line]:
    """first から last までの行（時刻の順の並びで、両端を含む）。"""
    keys = [(line.day_file, line.no) for line in lines]
    start, end = keys.index(first), keys.index(last)
    if start > end:
        raise ValueError(f"範囲の始まりが終わりより後: {first} → {last}")
    return lines[start : end + 1]


def conversation_refs(lines: list[Line]) -> tuple[str, ...]:
    """行の並びを、記録の場所（日のファイルと、続いた行番号の範囲）で書く。"""
    return refs_of((line.day_file, line.no) for line in lines)


def conversation_positions(page: Page) -> set[tuple[str, int]]:
    """ページが拠っている会話の行（日のファイル名, 行番号）。会話でない記録（継承した原本）は含めない。"""
    return positions_of(page.source)


def next_id(prefix: str, taken: set[str], *, width: int = 1) -> str:
    """prefix の後ろに、まだ使われていない番号（1から）を付けた id。"""
    n = 1
    while f"{prefix}-{n:0{width}d}" in taken:
        n += 1
    return f"{prefix}-{n:0{width}d}"


def episode_pages(
    lines: list[Line], spans: list[EpisodeSpan], *, structured_by: str, taken_ids: set[str] = frozenset()
) -> list[Page]:
    """会話の行（記録のすべての行、時刻の順。道具の行も含む）と区切りから、出来事の骨組みを作る。区切りは重なってはいけない。

    id は「ep-<始まりの日本時間の日付>-<その日の番号>」。taken_ids（もうあるページの id）の番号は使わない。
    """
    order = {(line.day_file, line.no): i for i, line in enumerate(lines)}
    pages: list[Page] = []
    taken: set[tuple[str, int]] = set()
    ids = set(taken_ids)
    for span in sorted(spans, key=lambda s: order[s.first]):
        inside = span_lines(lines, span.first, span.last)
        keys = {(line.day_file, line.no) for line in inside}
        if keys & taken:
            raise ValueError(f"区切りが重なっている: {span.first}")
        taken |= keys
        start = inside[0].ts.astimezone(JST)
        page_id = next_id(f"ep-{start:%Y-%m-%d}", ids, width=2)
        ids.add(page_id)
        pages.append(
            Page(
                id=page_id,
                kind="episode",
                start=start,
                end=inside[-1].ts.astimezone(JST),
                source=conversation_refs(inside),
                concepts=span.concepts,
                structured_by=structured_by,
            )
        )
    return pages


def link_neighbors(pages: list[Page]) -> list[Page]:
    """同じ種類のページを、時刻の順に前後でつなぐ（時刻のない覚え書きはつながない）。"""
    linked = {page.id: replace(page, prev="", next="") if page.start is not None else page for page in pages}
    for kind in ("episode", "diary", "note"):
        timeline = sorted((p for p in pages if p.kind == kind and p.start is not None), key=lambda p: (p.start, p.id))
        for before, after in zip(timeline, timeline[1:]):
            linked[before.id] = replace(linked[before.id], next=after.id)
            linked[after.id] = replace(linked[after.id], prev=before.id)
    return [linked[page.id] for page in pages]


def episode_evidence(lines: list[Line], page: Page, labels: dict[str, str]) -> list[Line]:
    """出来事のページが拠っている会話の行（Masterと住人の発言）。labels の話者だけ。"""
    wanted = conversation_positions(page)
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


# --- 眠りの間の整理（本人の脳が区切る） --------------------------------------------


def runs(lines: list[Line], day_of: Callable[[datetime], object]) -> list[list[Line]]:
    """続いた会話のかたまり。セッションが変わるか、30分以上あくか、日（day_of。Serina日）が変わったら切る。"""
    out: list[list[Line]] = []
    for line in lines:
        last = out[-1][-1] if out else None
        if (
            last is None
            or line.session != last.session
            or line.ts - last.ts >= SEGMENT_GAP
            or day_of(line.ts) != day_of(last.ts)
        ):
            out.append([line])
        else:
            out[-1].append(line)
    return out


def _segment_view(line: Line, labels: dict[str, str]) -> str:
    text = re.sub(r"\s+", " ", line.text).strip()
    if len(text) > SEGMENT_LINE_VIEW:
        text = text[:SEGMENT_LINE_VIEW] + "……"
    return f"{labels.get(line.speaker, line.speaker)}: {text}"


def windows(run: list[Line], labels: dict[str, str]) -> list[list[Line]]:
    """脳に一度に見せられる長さに分けた並び。"""
    out: list[list[Line]] = [[]]
    size = 0
    for line in run:
        length = len(_segment_view(line, labels)) + 6
        if out[-1] and size + length > SEGMENT_WINDOW_CHARS:
            out.append([])
            size = 0
        out[-1].append(line)
        size += length
    return out


def segment_prompt(window: list[Line], labels: dict[str, str], known: list[str]) -> str:
    record = "\n".join(f"{n} {_segment_view(line, labels)}" for n, line in enumerate(window, start=1))
    names = f"\n【これまでの記憶にある名前（同じものなら、この名前を使う）】{'、'.join(known)}\n" if known else ""
    return f"""あなたは、自分の記憶を整理しているところ。下は、マスターとあなたの会話の記録（行の頭の数字は行番号。「わたし」があなた）。
長い発言は頭だけを見せている。

【記録】
{record}
{names}
この会話を、出来事に区切って。ひとつの出来事は、ひとつの話題や目的のまとまり（ふつう4〜30行。2往復以上）。
話の流れが続いているあいだは、話題が少しずつ移っていても同じ出来事にする。話題が変わっていなければ、全部でひとつでいい。
それぞれの出来事に、あとで思い出す手がかりになる概念（人・場所・もの・話題・出来事の短い名前）を3〜8個。会話の中の言い方を使う。
次のJSONだけを返す。
{{"episodes": [{{"first": その出来事が始まる行番号, "concepts": [概念, ...]}}, ...]}}"""


def _concepts(raw, name_of: Callable[[str], str]) -> tuple[str, ...]:  # noqa: ANN001
    if isinstance(raw, str):
        raw = re.split(r"[、,，/／]", raw)
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        name = re.sub(r"\s+", " ", item).strip().strip("「」『』\"'")
        if not 1 <= len(name) <= CONCEPT_CHARS:
            continue
        name = name_of(name)
        if name not in out:
            out.append(name)
    return tuple(out[:MAX_CONCEPTS])


def parse_segments(answer: dict, n_lines: int, name_of: Callable[[str], str]) -> list[tuple[int, tuple[str, ...]]]:
    """脳の区切り → [(始まりの行番号, 概念)]。1行目から始まり、重ならず、行番号の順。MIN_EPISODE_LINES より短い出来事は前に含める。

    name_of は、概念の名前を呼び名の辞書の代表の名前にそろえる。
    """
    episodes = answer.get("episodes")
    if not isinstance(episodes, list) or not episodes:
        raise StructureRejected("episodes が空か、並びでない")
    starts: dict[int, tuple[str, ...]] = {}
    for item in episodes:
        if not isinstance(item, dict):
            continue
        try:
            first = int(item.get("first"))
        except (TypeError, ValueError):
            continue
        if 1 <= first <= n_lines and first not in starts:
            starts[first] = _concepts(item.get("concepts"), name_of)
    if not starts:
        raise StructureRejected("使える行番号がない")
    ordered = sorted(starts.items())
    merged: list[tuple[int, tuple[str, ...]]] = [(1, ordered[0][1])]  # 最初の出来事は1行目から
    for first, concepts in ordered[1:]:
        if first - merged[-1][0] < MIN_EPISODE_LINES:  # 前の出来事が短すぎるなら、区切らない
            merged[-1] = (merged[-1][0], tuple(dict.fromkeys(merged[-1][1] + concepts))[:MAX_CONCEPTS])
        else:
            merged.append((first, concepts))
    if len(merged) > 1 and n_lines - merged[-1][0] + 1 < MIN_EPISODE_LINES:  # 最後の出来事が短すぎるなら、前に含める
        last = merged.pop()
        merged[-1] = (merged[-1][0], tuple(dict.fromkeys(merged[-1][1] + last[1]))[:MAX_CONCEPTS])
    return merged


def segment(
    run: list[Line],
    *,
    ask: Callable[[str, dict, int], dict],
    labels: dict[str, str],
    known_in: Callable[[str], list[str]],
    name_of: Callable[[str], str],
    attempts: int = 3,
) -> list[EpisodeSpan]:
    """続いた会話のかたまりを、本人の脳で出来事に区切る。

    known_in(文) は、その文に出てくる、これまでの記憶の概念の名前（名前をそろえるために見せる）。name_of は parse_segments と同じ。
    脳が何度聞いても区切れなければ、窓ひとつをひとつの出来事にする（区切りは粗くなるが、記憶は失わない）。
    """
    spans: list[EpisodeSpan] = []
    for window in windows(run, labels):
        text = "\n".join(line.text for line in window)
        prompt = segment_prompt(window, labels, known_in(text))
        starts: list[tuple[int, tuple[str, ...]]] | None = None
        asking = prompt
        for attempt in range(attempts):
            try:
                starts = parse_segments(ask(asking, SEGMENT_SCHEMA, attempt), len(window), name_of)
                break
            except StructureRejected as e:
                asking = f"{prompt}\n\n（さっきの答えは使えなかった：{e}。上の形のJSONだけを返して）"
        if starts is None:
            starts = [(1, tuple(known_in(text)[:MAX_CONCEPTS]))]
        bounds = [first for first, _ in starts] + [len(window) + 1]
        for (first, concepts), end in zip(starts, bounds[1:]):
            a, b = window[first - 1], window[end - 2]
            spans.append(EpisodeSpan((a.day_file, a.no), (b.day_file, b.no), concepts))
    return spans
