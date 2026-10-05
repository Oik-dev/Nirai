"""眠り（docs/plans/長期記憶の作り直し.md §5）。起きていた間の会話の記録を、本人の記憶のページにする。

会話の最中は、記録して思い出すだけ。眠りの間に、本人の脳（Serinaなら手元のGemma）で次を行う。
1. 区切る：まだどのページにもなっていない会話（今の Serina 日より前のもの）を、出来事に区切って概念とつなぐ
   （structure.segment）。区切ったら、その日の日記のページの骨組みも置く。どのページにも、そのあいだの気持ちの記録から
   仕組みが芯の数を付ける（ピーク・エンド。core/feeling/feelings.py。気持ちの記録はその日のうちに全部そろっている）。
2. 書く：出来事ごとに本人の言葉を書く（writing.write_episode）。
3. 日記：その日の出来事のページと気持ちの流れ（そのときどきの本人の言葉）から、本人が日記を書く（writing.write_diary）。
4. つなぐ：ページの前後を結び直し、索引を作り直す。

どの段も、途中で止まってよい。何が済んだかはページそのものから分かる（記録のどの行がページになったか、
どのページに本人の言葉があるか）。次の眠りは、残っているところから続ける。日記の骨組みを出来事より先に置くのは、
区切りの途中で止まっても、その日の日記を書き忘れないようにするため。
関係・知識・今の自分・振り返りは、まだ眠りに入っていない（計画書 M5）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime

from mind.core.feeling.feelings import Feelings, flow_lines, peak_end
from mind.core.lifelog import Line, read_conversation
from mind.core.memory.memory import Memory, relink
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.structure import (
    JST,
    MASTER,
    MAX_CONCEPTS,
    conversation_positions,
    conversation_refs,
    episode_evidence,
    episode_pages,
    next_id,
    runs,
    segment,
    span_text,
)
from mind.core.memory.writing import Ask, WordsRejected, write_episode, write_diary
from mind.core.state.serina_day import serina_day_id

logger = logging.getLogger(__name__)

HAPPENINGS_CHARS = 3000  # 日記の材料にする、その日の出来事の長さの合計
STORY_IN_DIARY = 320  # 材料にする出来事1つの本文の長さ
FEELINGS_IN_DIARY = 12  # 日記の材料にする、その日の気持ちの言葉の数（多ければ等間隔に選ぶ）


@dataclass
class SleepReport:
    episodes: int = 0  # 新しく区切った出来事
    written: int = 0  # 本人の言葉を書いたページ（出来事と日記）
    failed: list[str] = field(default_factory=list)  # 書けなかったページ（次の眠りでもう一度）
    finished: bool = False  # 最後まで眠れたか（起こされたら False）

    @property
    def changed(self) -> bool:
        return bool(self.episodes or self.written)


def _day(at: datetime) -> date:
    return serina_day_id(at)


def unslept_lines(lines: list[Line], pages: list[Page], *, before: datetime) -> list[Line]:
    """まだどの出来事のページにもなっていない、before より前の会話の行。"""
    covered: set[tuple[str, int]] = set()
    for page in pages:
        if page.kind == "episode":
            covered |= conversation_positions(page)
    return [line for line in lines if line.ts < before and (line.day_file, line.no) not in covered]


def has_diary(pages: list[Page], day: date) -> bool:
    return any(page.kind == "diary" and page.start is not None and _day(page.start) == day for page in pages)


def with_affect(page: Page, feelings: Feelings | None) -> Page:
    """ページに、そのあいだの気持ちの記録から芯の数を付ける（記録がなければ付けない）。"""
    if feelings is None:
        return page
    return replace(page, affect=peak_end(feelings.during(conversation_positions(page))))


def diary_skeleton(day_lines: list[Line], *, structured_by: str, taken_ids: set[str]) -> Page:
    """その日の日記のページの骨組み。本文は空で、本人が眠りの間に書く。"""
    start = day_lines[0].ts.astimezone(JST)
    return Page(
        id=next_id(f"diary-{_day(start).isoformat()}", taken_ids),
        kind="diary",
        start=start,
        end=day_lines[-1].ts.astimezone(JST),
        source=conversation_refs(day_lines),
        concepts=(),
        structured_by=structured_by,
    )


def happenings(day_episodes: list[Page], lines: list[Line], labels: dict[str, str]) -> str:
    """日記の材料：その日の出来事のページ（本人の言葉。まだなければ記録の頭）。"""
    share = max(120, HAPPENINGS_CHARS // max(1, len(day_episodes)))
    out = []
    for page in day_episodes:
        at = page.start.astimezone(JST).strftime("%H:%M")
        if page.written:
            story = page.body.split("\n\nそのときの言葉：")[0]
            text = f"- {at} {page.title}：{page.gist}\n  {_clip(story, min(share, STORY_IN_DIARY))}"
        else:
            record = span_text(episode_evidence(lines, page, labels), labels)
            text = f"- {at} （まだ言葉にしていない出来事）\n  {_clip(record, min(share, STORY_IN_DIARY))}"
        out.append(text)
    return "\n".join(out)


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "……"


def _weekday_label(day: date) -> str:
    return f"{day.year}年{day.month}月{day.day}日（{'月火水木金土日'[day.weekday()]}）"


def sleep(
    memory: Memory,
    *,
    before: datetime,
    ask: Ask,
    persona: str,
    brain: str,
    feelings: Feelings | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    today: date | None = None,
    progress: Callable[[str], None] = logger.info,
) -> SleepReport:
    """眠る。before より前の、まだページになっていない会話を記憶にする（ふつう before は今の Serina 日の始まり）。

    ask は本人の脳への問い方、persona は本人の人格の文、brain は脳の名前（ページの書き手として残す）。
    feelings は気持ちの記録（ページの芯の数と、日記の材料の気持ちの流れ）。なければ、どちらもなしで眠る。
    should_stop() が真になったら、区切りのいいところで止まる（Masterが話しかけてきたときなど）。
    """
    idea = memory.idea
    report = SleepReport()
    labels = {MASTER: "マスター", idea.name: "わたし"}
    signed = f"{brain} ({(today or datetime.now(JST).date()).isoformat()})"

    # 1. 区切る
    with memory.pages_lock:
        todo = unslept_lines(read_conversation(idea.conversation), load_pages(idea.memory), before=before)
    for run in runs(todo, _day):
        if should_stop():
            return report
        day = _day(run[0].ts)
        spans = segment(run, ask=ask, labels=labels, known_in=memory.known_in, name_of=memory.name_of)
        with memory.pages_lock:
            lines = read_conversation(idea.conversation)
            pages = load_pages(idea.memory)
            if [line for line in unslept_lines(lines, pages, before=before) if line in run] != run:
                continue  # 区切っている間に、Masterがこの記録を消した。残りは次の眠りで区切る
            taken = {page.id for page in pages}
            if not has_diary(pages, day):
                day_lines = [line for line in todo if _day(line.ts) == day]
                diary = with_affect(diary_skeleton(day_lines, structured_by=signed, taken_ids=taken), feelings)
                write_page(idea.memory, diary)
                taken.add(diary.id)
            for page in episode_pages(lines, spans, structured_by=signed, taken_ids=taken):
                write_page(idea.memory, with_affect(page, feelings))
                report.episodes += 1
        progress(f"眠り：{day.isoformat()} の会話を{len(spans)}つの出来事に区切った")
    with memory.pages_lock:
        relink(idea.memory)

    # 2. 書く（出来事が先、日記はその日の出来事のあと）
    with memory.pages_lock:
        pages = load_pages(idea.memory)
    unwritten = [p for p in pages if not p.written and p.kind == "episode"]
    unwritten += [p for p in pages if not p.written and p.kind == "diary" and not p.body]
    for page in unwritten:
        if should_stop():
            _rebuild_if_changed(memory, report, progress)
            return report
        lines = read_conversation(idea.conversation)
        try:
            if page.kind == "episode":
                done = _write_episode(page, memory, lines, labels, persona=persona, ask=ask, signed=signed)
            else:
                done = _write_diary(page, memory, lines, labels, persona=persona, ask=ask, signed=signed, feelings=feelings)
        except WordsRejected as e:
            report.failed.append(page.id)
            progress(f"眠り：{page.id} を書けなかった（次の眠りでもう一度）: {e}")
            continue
        with memory.pages_lock:
            if not page.path_in(idea.memory).exists():
                continue  # 書いている間に、Masterが消した記録のページだった
            write_page(idea.memory, done)
        report.written += 1
        progress(f"眠り：{done.id}「{done.title}」を書いた")

    _rebuild_if_changed(memory, report, progress)
    report.finished = True
    return report


def _write_episode(page: Page, memory: Memory, lines: list[Line], labels: dict[str, str], *, persona: str, ask: Ask, signed: str) -> Page:
    with memory.pages_lock:
        pages = {p.id: p for p in load_pages(memory.idea.memory)}
    prev = pages.get(page.prev)
    return write_episode(
        page,
        episode_evidence(lines, page, labels),
        persona=persona,
        labels=labels,
        prev_title=prev.title if prev else "",
        ask=ask,
        written_by=signed,
    )


def _write_diary(
    page: Page,
    memory: Memory,
    lines: list[Line],
    labels: dict[str, str],
    *,
    persona: str,
    ask: Ask,
    signed: str,
    feelings: Feelings | None,
) -> Page:
    day = _day(page.start)
    with memory.pages_lock:
        episodes = [p for p in load_pages(memory.idea.memory) if p.kind == "episode" and p.start and _day(p.start) == day]
    return write_diary(
        replace(page, concepts=diary_concepts(episodes)),
        persona=persona,
        day=_weekday_label(day),
        happenings=happenings(episodes, lines, labels),
        feelings=flow_lines(feelings.during(conversation_positions(page)), FEELINGS_IN_DIARY) if feelings else "",
        ask=ask,
        written_by=signed,
    )


def diary_concepts(day_episodes: list[Page]) -> tuple[str, ...]:
    """日記につなぐ概念：その日の出来事によく出てきたものから MAX_CONCEPTS まで（日記を何にでも反応するハブにしない）。"""
    counts: dict[str, int] = {}
    for page in day_episodes:
        for concept in page.concepts:
            counts[concept] = counts.get(concept, 0) + 1
    order = sorted(counts, key=lambda c: -counts[c])  # 同じ数なら、先に出てきた順（sorted は安定）
    return tuple(order[:MAX_CONCEPTS])


def _rebuild_if_changed(memory: Memory, report: SleepReport, progress: Callable[[str], None]) -> None:
    if report.changed:
        with memory.pages_lock:
            memory.rebuild_index(progress=progress)
