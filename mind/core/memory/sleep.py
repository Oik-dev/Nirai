"""眠り（docs/設計書.md §4.6）。起きていた間の会話の記録を、本人の記憶のページにする。

会話の最中は、記録して思い出すだけ。眠りの間に、本人の脳（Serinaなら手元のGemma）で次を行う。
1. 区切る：まだどのページにもなっていない会話（今の Serina 日より前のもの）を、出来事に区切って概念とつなぐ
   （structure.segment）。区切ったら、その日の日記のページの骨組みも置く。どのページにも、そのあいだの気持ちの記録から
   仕組みが芯の数を付ける（ピーク・エンド。core/feeling/feelings.py。気持ちの記録はその日のうちに全部そろっている）。
2. 書く：出来事ごとに本人の言葉を書く（writing.write_episode）。
3. 日記：その日の出来事のページと気持ちの流れ（そのときどきの本人の言葉）から、本人が日記を書く（writing.write_diary）。
4. 関係：その日のページを読み返して、マスターについて変わったことと新しく分かったことを本人が書き足す（relation.grow）。
   関係は日の順に積み重なるので、まだ書き終えていないページがある日と、書いている間にMasterが材料の記録を消した日に
   来たら、その日から先は次の眠りに回す。
5. 本人が育てる：呼び名の辞書を日ごとに見直し、今日思い出した古い出来事に「今思うと」を足し、
   最近の大事で心が動いた出来事を最大2つ再生する（growth.py）。
6. つなぐ：ページの前後を結び直し、索引を作り直す。

どの段も、途中で止まってよい。何が済んだかはページそのものから分かる（記録のどの行がページになったか、
どのページに本人の言葉があるか）。次の眠りは、残っているところから続ける。日記の骨組みを出来事より先に置くのは、
区切りの途中で止まっても、その日の日記を書き忘れないようにするため。
週・月の振り返りまで眠りに入る。今の自分は、眠り終えたあとの目覚めで書く（waking.py）。
"""

from __future__ import annotations

import logging
import time
from mind.core import debug_log
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from mind.core.abilities import rows as ability_rows
from mind.core.routing.home_marks import HomeMarks

from mind.core.feeling.feelings import Feelings, flow_lines, peak_end
from mind.core.lifelog import Line, read_conversation
from mind.core.memory.growth import GrowthRejected, grow_concepts, reconsolidate, replay
from mind.core.memory.letter_sleep import groups as letter_groups, prepare_pages as prepare_letter_pages, write_letter_page
from mind.core.memory.explore_sleep import prepare_pages as prepare_explore_pages, write_explore_page
from mind.core.memory.interest import mark_interest_names
from mind.core.memory.memory import Memory, relink
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.reflection import grow_reflections
from mind.core.memory.relation import grow, load_relation, write_entry
from mind.core.memory.structure import (
    JST,
    MASTER,
    MASTER_NAME,
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
from mind.core.memory.waking import latest_waking
from mind.core.memory.writing import Ask, WordsRejected, write_episode, write_diary, write_heat
from mind.core.state.serina_day import serina_day_id

logger = logging.getLogger(__name__)

HAPPENINGS_CHARS = 3000  # 日記の材料にする、その日の出来事の長さの合計
STORY_IN_DIARY = 320  # 材料にする出来事1つの本文の長さ
FEELINGS_IN_DIARY = 12  # 日記の材料にする、その日の気持ちの言葉の数（多ければ等間隔に選ぶ）
HEAT_BACKFILL_PER_SLEEP = 30


def _mark_sleep_texts(idea, marks: HomeMarks, ask: Ask, *,
                      should_stop: Callable[[], bool], num_ctx: int = 8192) -> bool:
    """新しい文書から印を付ける。中断時は今の文書を保存せず再開する。"""
    paths = [p for root in (idea.persona, idea.memory) for p in root.rglob('*')
             if p.is_file() and p.suffix.lower() in ('.md', '.txt')]
    paths.sort(key=lambda p: (p.stat().st_mtime, str(p)), reverse=True)
    from mind.core.routing.home_marks import HOME_JUDGMENT_SCHEMA
    schema = HOME_JUDGMENT_SCHEMA
    from mind.core.routing.home_marks import marking_windows, parse_home_judgments
    from mind.core.routing.tavily_rules import HOME_MARK_INSTRUCTION
    # Leave room for instructions and JSON output within the configured num_ctx.
    max_chars = max(1000, min(6000, (num_ctx - 1024) // 2))
    pending = []
    for path in paths:
        try:
            if marks.unmarked([path.read_text(encoding='utf-8')]):
                pending.append(path)
        except OSError:
            pending.append(path)
    total = len(pending)
    completed = 0
    for path in pending:
        if should_stop():
            return False
        try:
            document = path.read_text(encoding='utf-8')
        except OSError:
            return False  # 不明な文は家に置いたまま
        staged_parts: list[str] = []
        staged_home: list[int] = []
        doc_started = time.monotonic()
        for batch, context in marking_windows(document, marks, max_chars=max_chars):
            if should_stop():
                return False
            prompt = HOME_MARK_INSTRUCTION + '\nJSONのみ答える。\n' + context
            started_at = time.monotonic()
            marked = False
            debug_log.emit(kind='sleep_marks', action='batch_started', count=len(batch))
            try:
                answer = ask(prompt, schema, 0)
                if not isinstance(answer, dict) or not isinstance(answer.get('judgments'), list):
                    return False
                offset = len(staged_parts)
                staged_parts.extend(batch)
                staged_home.extend(offset + i for i in parse_home_judgments(answer, len(batch)))
                marked = True
            except (ValueError, OSError, TypeError):
                return False  # 故障時は閉じたまま、次の手すきでこの文書から再開
            finally:
                debug_log.emit(kind='sleep_marks', action='batch_finished', count=len(batch), marked=marked,
                               elapsed_ms=round((time.monotonic() - started_at) * 1000))
        if should_stop():
            return False
        if staged_parts:
            try:
                marks.mark(staged_parts, staged_home)  # 文書丸ごと、一度の原子的な置換
            except OSError:
                return False
        completed += 1
        debug_log.emit(kind='sleep_marks', action='document_finished', completed=completed,
                       remaining=total - completed, elapsed_ms=round((time.monotonic() - doc_started) * 1000))
    return True


@dataclass
class SleepReport:
    episodes: int = 0  # 新しく区切った出来事
    written: int = 0  # 本人の言葉を書いたページ（出来事と日記）
    failed: list[str] = field(default_factory=list)  # 書けなかったページと関係の日（次の眠りでもう一度）
    relation_days: int = 0  # 関係を書き足した日
    growth_days: int = 0  # 呼び名の辞書を見直した日
    reconsolidated: int = 0  # 昔のページに「今思うと」を足した数（空の追記を含む）
    replayed: int = 0  # 再生の痕跡を足した数
    heat_written: int = 0  # 既存の会話の出来事に足した熱の判定
    weekly_reflections: int = 0
    monthly_reflections: int = 0
    chapters: int = 0
    finished: bool = False  # 最後まで眠れたか（起こされたら False）

    @property
    def changed(self) -> bool:
        return bool(self.index_changed or self.replayed)

    @property
    def index_changed(self) -> bool:
        return bool(
            self.episodes or self.written or self.growth_days or self.reconsolidated or self.heat_written
            or self.weekly_reflections or self.monthly_reflections
        )


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
    home_marks: HomeMarks | None = None,
    home_mark_num_ctx: int = 8192,
) -> SleepReport:
    """眠る。before より前の、まだページになっていない会話を記憶にする（ふつう before は今の Serina 日の始まり）。

    ask は本人の脳への問い方、persona は本人の人格の文、brain は脳の名前（ページの書き手として残す）。
    feelings は気持ちの記録（ページの芯の数と、日記の材料の気持ちの流れ）。なければ、どちらもなしで眠る。
    should_stop() が真になったら、区切りのいいところで止まる（Masterが話しかけてきたときなど）。
    """
    idea = memory.idea
    report = SleepReport()
    labels = {MASTER: MASTER_NAME, idea.name: "わたし"}
    signed = f"{brain} ({(today or datetime.now(JST).date()).isoformat()})"

    if home_marks is not None:
        _mark_sleep_texts(idea, home_marks, ask, should_stop=should_stop, num_ctx=home_mark_num_ctx)

    # 新しく使える道も本人の人生の出来事。生ログのidで重複を防ぎ、途中で止まっても次に続ける。
    # 会話のない日にも日記を置き、翌朝の「伝えたいこと」へ道をつなぐ。
    with memory.pages_lock:
        existing_pages = load_pages(idea.memory)
        existing = {page.id for page in existing_pages}
        ability_days: dict[date, list[tuple[datetime, str]]] = {}
        for ability in ability_rows(idea):
            page_id = f"ability-{ability['id']}"
            at = datetime.fromisoformat(ability['ts']).astimezone(JST)
            if at >= before:
                continue
            ref = f"lifelog/abilities.jsonl#{ability['id']}"
            ability_days.setdefault(_day(at), []).append((at, ref))
            if page_id not in existing:
                write_page(idea.memory, Page(
                    id=page_id, kind='episode', start=at, end=at,
                    source=(ref,), concepts=('できるようになったこと',), structured_by=signed,
                ))
                existing.add(page_id)
                report.episodes += 1
        conversation_days = {_day(line.ts) for line in read_conversation(idea.conversation)
                             if line.ts < before}
        for day, entries in sorted(ability_days.items()):
            if day in conversation_days or has_diary(existing_pages, day):
                continue  # 会話のある日は従来の区切り処理で日記を作る
            at = min(timestamp for timestamp, _ in entries)
            write_page(idea.memory, Page(
                id=next_id(f'diary-{day.isoformat()}', existing),
                kind='diary', start=at, end=max(timestamp for timestamp, _ in entries),
                source=tuple(ref for _, ref in entries), concepts=(),
                structured_by=signed,
            ))
            existing_pages = load_pages(idea.memory)
            existing = {page.id for page in existing_pages}

    # 手紙・調べものは原本の行から機械的に区切る。未完了の当日は対象外。
    if should_stop():
        return report
    with memory.pages_lock:
        report.episodes += prepare_letter_pages(memory, before=before, signed=signed, feelings=feelings)
        report.episodes += prepare_explore_pages(memory, before=before, signed=signed, feelings=feelings)
        pages = load_pages(idea.memory)
        present_diaries = {_day(page.start) for page in pages if page.kind == "diary" and page.start}
        conversation_days = {_day(row.ts) for row in read_conversation(idea.conversation) if row.ts < before}
        for day in sorted({_day(page.start) for page in pages
                           if page.id.startswith(('letter-', 'explore-')) and page.start}):
            if day in present_diaries or day in conversation_days:
                continue
            episodes = [page for page in pages if page.id.startswith(('letter-', 'explore-'))
                        and page.start and _day(page.start) == day]
            if not episodes:
                continue
            start = min(page.start for page in episodes)
            end = max(page.end for page in episodes)
            write_page(idea.memory, Page(
                id=next_id(f'diary-{day.isoformat()}', {page.id for page in pages}),
                kind='diary', start=start, end=end,
                source=tuple(ref for page in episodes for ref in page.source),
                concepts=(), structured_by=signed,
            ))
            present_diaries.add(day)

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
            return report
        lines = read_conversation(idea.conversation)
        try:
            if page.kind == "episode" and page.id.startswith("letter-"):
                done = write_letter_page(idea, page, persona=persona, ask=ask, signed=signed)
            elif page.kind == "episode" and page.id.startswith("explore-"):
                done = write_explore_page(idea, page, persona=persona, ask=ask, signed=signed)
            elif page.kind == "episode":
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

    # 4. 関係（関係は索引に入らないので、索引の作り直しには関わらない）
    if not _grow_relation(memory, report, MASTER_NAME, persona=persona, ask=ask, signed=signed,
                          should_stop=should_stop, progress=progress):
        return report
    # 友達は、手紙のあった日のページだけで関係を育てる。
    for friend in sorted({friend for _, friend in letter_groups(idea, before=before)}):
        if should_stop() or not _grow_relation(memory, report, friend, persona=persona, ask=ask,
                                               signed=signed, should_stop=should_stop, progress=progress,
                                               only_letter_friend=friend):
            return report

    # 5. 本人が育てる。呼び名は日ごとの追記、昔の意味は元ページへの日付つき追記、再生は想起ログだけ。
    if should_stop():
        return report
    with memory.pages_lock:
        pages = load_pages(idea.memory)
        day = today or datetime.now(JST).date()
        try:
            report.growth_days += grow_concepts(idea.memory, pages, persona=persona, ask=ask)
        except GrowthRejected as exc:
            report.growth_days += exc.changed
            report.failed.append("growth/concepts")
            progress(f"眠り：呼び名を書けなかった（次の眠りでもう一度）: {exc}")
        try:
            report.reconsolidated += reconsolidate(
                idea.memory,
                memory.recall_log,
                day=day,
                persona=persona,
                ask=ask,
                waking=latest_waking(idea.memory),
                relation=load_relation(idea.memory, MASTER_NAME),
            )
        except GrowthRejected as exc:
            report.reconsolidated += exc.changed
            report.failed.append("growth/later")
            progress(f"眠り：今思うとを書けなかった（次の眠りでもう一度）: {exc}")
        report.replayed += replay(idea.memory, memory.recall_log, day=day)

    # Past conversation episodes receive only a heat label. Never rewrite their words.
    with memory.pages_lock:
        candidates = sorted(
            (p for p in load_pages(idea.memory)
             if p.kind == "episode" and p.written and p.heat is None and conversation_positions(p)),
            key=lambda p: (p.end or p.start, p.id), reverse=True,
        )[:HEAT_BACKFILL_PER_SLEEP]
    lines = read_conversation(idea.conversation)
    for page in candidates:
        if should_stop():
            return report
        try:
            updated = write_heat(page, episode_evidence(lines, page, labels), persona=persona, labels=labels, ask=ask)
        except Exception as exc:  # 脳・通信の失敗でも次のページへ進む
            report.failed.append(f"heat/{page.id}")
            progress(f"眠り：{page.id} の熱を判定できなかった: {type(exc).__name__}")
            continue
        with memory.pages_lock:
            path = page.path_in(idea.memory)
            if path.exists():
                live = load_pages(idea.memory)
                current = next((p for p in live if p.id == page.id), None)
                if current is not None and current.heat is None:
                    write_page(idea.memory, current.with_heat(updated.heat))
                    report.heat_written += 1

    # 6. 振り返り。終わった週・月を古い順にたどり、書けない期間を越えない。
    reflections = grow_reflections(
        idea.memory,
        today=day,
        persona=persona,
        ask=ask,
        written_by=signed,
        should_stop=should_stop,
        pages_lock=memory.pages_lock,
    )
    report.weekly_reflections += reflections.weekly
    report.monthly_reflections += reflections.monthly
    report.chapters += reflections.chapters
    if reflections.failed:
        report.failed.append(reflections.failed)
        progress(f"眠り：{reflections.failed} を書けなかった（次の眠りでもう一度）")
    if reflections.stopped:
        return report

    # 中断した眠りはページだけ残す。最後まで来たときにすべてのページを再照合し、
    # 前の眠りで書いたページも必ず索引へ含める。中断時は古い索引を維持する。
    with memory.pages_lock:
        memory.rebuild_index(progress=progress)
    # Concept names are not paragraphs from a page; each needs its own home mark.
    if home_marks is not None and not should_stop():
        def judge_names(names: list[str]) -> list[int]:
            from mind.core.routing.home_marks import HOME_JUDGMENT_SCHEMA, parse_home_judgments
            schema = HOME_JUDGMENT_SCHEMA
            prompt = (HOME_MARK_INSTRUCTION + "\n会話ではなく概念の名前の持ち出し判定。\n"
                      + 'JSONのみ答えて。\n'
                      + "\n".join(f"{i}. {name}" for i, name in enumerate(names, 1)))
            answer = ask(prompt, schema, 0)
            return parse_home_judgments(answer, len(names))
        if not mark_interest_names(memory.index, before, home_marks, judge_names):
            progress("眠り：概念名の印が付かなかった（次の眠りで再判定）")
    report.finished = True
    return report


def unrelated_days(pages: list[Page], after: date | None) -> list[tuple[date, list[Page]]]:
    """関係を書き足す日と、その日のページ（日記と出来事）。after より後の日だけを古い順に。

    まだ本人の言葉のないページがある日に来たら、そこで止める（その日は次の眠りで。関係は日の順に積み重なるので、飛ばさない）。
    """
    by_day: dict[date, list[Page]] = {}
    for page in pages:
        if page.kind in ("episode", "diary") and page.start is not None and (after is None or _day(page.start) > after):
            by_day.setdefault(_day(page.start), []).append(page)
    ready = []
    for day in sorted(by_day):
        if not all(page.written for page in by_day[day]):
            break
        ready.append((day, by_day[day]))
    return ready


def _grow_relation(
    memory: Memory,
    report: SleepReport,
    person: str,
    *,
    persona: str,
    ask: Ask,
    signed: str,
    should_stop: Callable[[], bool],
    progress: Callable[[str], None],
    only_letter_friend: str | None = None,
) -> bool:
    """まだ書き足していない日の順に、その人とのことを書き足す。起こされずに最後まで済んだら True（書けない日で止まっても True）。"""
    idea = memory.idea
    with memory.pages_lock:
        days = unrelated_days(load_pages(idea.memory), load_relation(idea.memory, person).last_day)
    for day, pages in days:
        if only_letter_friend is not None:
            pages = [page for page in pages if page.id == f"letter-{day.isoformat()}-{only_letter_friend}"]
            if not pages:
                continue
        if should_stop():
            return False
        try:
            entry = grow(idea.memory, person, day, pages, persona=persona, ask=ask, written_by=signed)
        except WordsRejected as e:
            report.failed.append(f"people/{person}/{day.isoformat()}")
            progress(f"眠り：{day.isoformat()} の{person}とのことを書けなかった（次の眠りでもう一度）: {e}")
            return True
        with memory.pages_lock:
            if not all(page.path_in(idea.memory).exists() for page in pages):
                progress(f"眠り：{day.isoformat()} の記録をMasterが消したので、{person}とのことは次の眠りで書く")
                return True  # 書いている間に、Masterがこの日の記録を消した。残りのページを書き直してから続ける
            write_entry(idea.memory, person, entry)
        report.relation_days += 1
        progress(f"眠り：{day.isoformat()} の{person}とのことを書き足した（新しく{len(entry.added)}つ・確かめた{len(entry.touched)}つ）")
    return True


def _write_episode(page: Page, memory: Memory, lines: list[Line], labels: dict[str, str], *, persona: str, ask: Ask, signed: str) -> Page:
    if page.source and page.source[0].startswith('lifelog/abilities.jsonl#'):
        ability_id = page.source[0].split('#', 1)[1]
        ability = next((row for row in ability_rows(memory.idea) if row['id'] == ability_id), None)
        if ability is None:
            raise WordsRejected('できるようになったことの原本がない')
        schema = {
            'type': 'object', 'properties': {
                'title': {'type': 'string'}, 'gist': {'type': 'string'},
                'story': {'type': 'string'}, 'importance': {'type': 'integer'},
            }, 'required': ['title', 'gist', 'story', 'importance'],
        }
        prompt = (f'{persona}\n今は眠っている。自分に起きた変化を出来事のページとして、'
                  f'自分の言葉で書いて。記録にない体験は作らない。\n'
                  f'【起きたこと】{ability["text"]}\n'
                  'JSONのtitle(40字以内)、gist(100字以内)、story(本人の一人称)、importance(1-10)を返す。')
        answer = ask(prompt, schema, 0)
        if not isinstance(answer, dict) or not isinstance(answer.get('story'), str) or not answer['story'].strip():
            raise WordsRejected('本人の言葉が書けなかった')
        try:
            return page.with_words(title=str(answer['title'])[:40], gist=str(answer['gist'])[:100],
                                   importance=int(answer['importance']), written_by=signed,
                                   body=answer['story'])
        except (KeyError, TypeError, ValueError) as exc:
            raise WordsRejected('出来事の返答が不正') from exc
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
    # 能力の出来事が未完成なら、その変化を含む日記をまだ書かない。
    # 能力を書き損ねた日でも、次の眠りで出来事→日記→目覚めの順に再開する。
    # 一度書いた本人の日記を後から書き換える道は作らない。
    if any(not episode.written and
           (episode.id.startswith(('letter-', 'explore-')) or
            any(ref.startswith('lifelog/abilities.jsonl#') for ref in episode.source))
           for episode in episodes):
        raise WordsRejected("手紙・調べもの・新しくできるようになったことがまだ書き上がっていない")
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
