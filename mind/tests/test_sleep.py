"""眠り（core/memory/sleep.py）と、眠りの間の整理（core/memory/structure.py の segment）のテスト。

守るもの：
- 眠りは、今の Serina 日より前の、まだどのページにもなっていない会話だけをページにする（今日の会話は手元に残る）。
- 区切りはセッションの切れ目・30分以上の間・日の変わり目でかならず切れ、その中は本人の脳が区切る。脳の答えは入口で整える。
  脳が何度聞いても区切れなくても、会話は記憶から落ちない（ひとつの出来事になる）。
- その日の出来事を書いてから、その日の日記を書く。日記の材料は出来事のページと、その日の気持ちの流れ（本人の言葉）。
- ページの芯の数は、そのあいだの気持ちの記録から仕組みが決める（ピーク・エンド）。気持ちの記録がなければ付けない。
- 途中で起こされても、次の眠りで続きから（同じ会話を二度ページにしない・日記を書き忘れない）。
- 書けなかったページは、言葉のないまま残り、次の眠りでもう一度書く。
- 区切っている間にMasterがその記録を消したら、その区切りは使わない。
- 眠り終えたら、新しいページを思い出せる（次の Serina 日から）。
LLM不要（脳の替え玉と、言葉の有無だけで決まる埋め込み）。
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import load_thresholds
from mind.core.feeling.appraisal import Appraisal
from mind.core.feeling.feelings import Feelings
from mind.core.idea import Idea
from mind.core.lifelog import ConversationLog, FeelingLog, Line, read_conversation
from mind.core.memory.memory import Memory
from mind.core.memory.page import load_pages
from mind.core.memory.recall import Cue
from mind.core.memory.sleep import sleep, unslept_lines
from mind.core.memory.structure import (
    SEGMENT_SCHEMA,
    StructureRejected,
    conversation_refs,
    parse_segments,
    runs,
    segment,
)
from mind.core.memory.writing import DIARY_SCHEMA, EPISODE_SCHEMA
from mind.core.state.serina_day import serina_day_id, serina_day_start

JST = timezone(timedelta(hours=9))
LABELS = {"Master": "マスター", "Serina": "わたし"}


def _at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


# --- 区切り -------------------------------------------------------------------


def _line(no: int, at: datetime, text: str, *, session: str = "s1", speaker: str = "Master") -> Line:
    return Line(at.astimezone(JST).strftime("%Y-%m-%d"), no, at, session, speaker, text)


def test_runs_break_at_sessions_long_pauses_and_the_serina_day() -> None:
    base = _at("2026-10-04", "06:30")
    lines = [
        _line(1, base, "a"),
        _line(2, base + timedelta(minutes=10), "b"),
        _line(3, base + timedelta(minutes=35), "c"),  # 7時を過ぎた：次の Serina 日
        _line(4, base + timedelta(minutes=40), "d", session="s2"),  # セッションが変わった
        _line(5, base + timedelta(minutes=80), "e", session="s2"),  # 30分以上あいた
    ]
    assert [[line.text for line in run] for run in runs(lines, serina_day_id)] == [["a", "b"], ["c"], ["d"], ["e"]]


def test_conversation_refs_name_continuous_line_ranges() -> None:
    at = _at("2026-10-04", "20:00")
    lines = [_line(n, at, "x") for n in (1, 2, 3, 5, 6)]
    assert conversation_refs(lines) == (
        "lifelog/conversation/2026-10-04.jsonl#1-3",
        "lifelog/conversation/2026-10-04.jsonl#5-6",
    )


def test_parse_segments_tidies_the_brains_answer() -> None:
    """どこで区切るかは脳、出来事の大きさの下限（4行＝2往復）は仕組み。短すぎる区切りは前に含める。"""
    names = {"約束の海": "高野漁港"}
    answer = {"episodes": [
        {"first": 1, "concepts": ["仕事", "  約束の海 "]},
        {"first": 3, "concepts": ["ラーメン"]},  # 1〜2行目だけの出来事になる → 前に含める
        {"first": 99, "concepts": ["ない行"]},
        {"first": 6, "concepts": "宮古島、 海"},
        {"first": 11, "concepts": ["最後の2行"]},  # 最後が2行だけ → 前に含める
    ]}
    starts = parse_segments(answer, 12, lambda name: names.get(name, name))
    assert starts == [(1, ("仕事", "高野漁港", "ラーメン")), (6, ("宮古島", "海", "最後の2行"))]
    assert parse_segments({"episodes": [{"first": 3, "concepts": []}]}, 10, str) == [(1, ())]  # 最初は1行目から
    assert parse_segments({"episodes": [{"first": 1, "concepts": []}, {"first": 2, "concepts": []}]}, 2, str) == [(1, ())]
    with pytest.raises(StructureRejected):
        parse_segments({"episodes": []}, 10, str)


def test_segment_falls_back_to_one_episode_when_the_brain_cannot(tmp_path: Path) -> None:
    at = _at("2026-10-04", "20:00")
    run = [_line(n, at + timedelta(minutes=n), f"高野漁港の話{n}") for n in range(1, 5)]
    asked = []

    def ask(prompt: str, schema: dict, attempt: int) -> dict:
        asked.append(attempt)
        return {"episodes": "わからない"}

    spans = segment(run, ask=ask, labels=LABELS, known_in=lambda text: ["高野漁港"], name_of=str)
    assert asked == [0, 1, 2]
    assert len(spans) == 1 and spans[0].first == (run[0].day_file, 1) and spans[0].last == (run[0].day_file, 4)
    assert spans[0].concepts == ("高野漁港",)


# --- 眠り ---------------------------------------------------------------------


def _embed(text: str) -> list[float]:
    words = ["高野漁港", "海", "仕事", "ラーメン", "日記"]
    return [1.0 if w in text else 0.0 for w in words] + [0.3]


class Brain:
    """脳の替え玉。区切りは「区切り」を含む行で切り、出来事と日記には決まった言葉を返す。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_titles: set[str] = set()
        self.on_segment = None
        self.diary_prompts: list[str] = []

    def __call__(self, prompt: str, schema: dict, attempt: int) -> dict:
        if schema is SEGMENT_SCHEMA:
            self.calls.append("segment")
            if self.on_segment:
                self.on_segment()
            record = prompt.split("【記録】\n")[1].split("\n\n")[0].splitlines()
            firsts = [1] + [int(row.split()[0]) for row in record if "区切り" in row and not row.startswith("1 ")]
            return {"episodes": [{"first": n, "concepts": ["高野漁港" if "海" in record[n - 1] else "仕事"]} for n in firsts]}
        if schema is EPISODE_SCHEMA:
            self.calls.append("episode")
            title = "海の約束" if "海" in prompt.split("【記録】")[1] else "仕事の話"
            if title in self.fail_titles:
                return {"title": 3}
            return {"title": title, "gist": f"{title}をした。", "story": f"わたしは{title}をした。" * 8,
                    "quotes": [1], "importance": 7}
        if schema is DIARY_SCHEMA:
            self.calls.append("diary")
            self.diary_prompts.append(prompt)
            return {"diary": "今日はマスターと話した日記。" * 8, "title": "話した日", "gist": "話した。",
                    "importance": 6}
        raise AssertionError(schema)


@pytest.fixture
def idea(tmp_path: Path) -> Idea:
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    return Idea.open(root)


def _talk(idea: Idea, at: datetime, *texts: str, session: str = "s1") -> None:
    log = ConversationLog(idea.conversation)
    for n, text in enumerate(texts):
        speaker = "Master" if n % 2 == 0 else "Serina"
        log.append(ts=(at + timedelta(minutes=n)).astimezone(timezone.utc).isoformat(), session=session, speaker=speaker, text=text)


def _memory(idea: Idea) -> Memory:
    return Memory(idea, embed=_embed, embed_model="fake")


NOW = _at("2026-10-05", "09:00")
BEFORE = serina_day_start(serina_day_id(NOW))


def _sleep(memory: Memory, brain: Brain, **kwargs):  # noqa: ANN003, ANN202
    return sleep(
        memory, before=BEFORE, ask=brain, persona="人格", brain="test-brain", today=date(2026, 10, 5),
        progress=lambda _m: None, **kwargs,
    )


def _felt_yesterday(idea: Idea) -> Feelings:
    """昨日の会話（_yesterday_and_today）の往復ごとに、本人が感じたことを残す。海の話で大きく心が動いた。"""
    feelings = Feelings(FeelingLog(idea.feeling), load_thresholds().feeling)
    felt = [
        (1, "海の話が楽しい", "うれしい", "少し動いた"),
        (3, "また海に行けたらいいな", "とてもうれしい", "大きく動いた"),
        (5, "お仕事おつかれさま", "どちらでもない", "落ち着いた"),
        (7, "早く休んでほしい", "どちらでもない", "落ち着いた"),
    ]
    for first, words, valence, arousal in felt:
        feelings.feel(
            Appraisal(feeling=words, valence=valence, arousal=arousal, distance="近づいた"),
            source=[f"lifelog/conversation/2026-10-04.jsonl#{first}-{first + 1}"],
            at=_at("2026-10-04", f"20:0{first}") + timedelta(seconds=30),
        )
    return feelings


def _yesterday_and_today(idea: Idea) -> None:
    _talk(
        idea, _at("2026-10-04", "20:00"),
        "海の話をしよう", "うん、高野漁港の海", "あの海、また見たいね", "絶対に行こうね",
        "区切り：仕事の話", "お疲れさま", "明日も早いんだ", "無理しないでね",
    )
    _talk(idea, _at("2026-10-05", "08:00"), "おはよう", "おはよう！")  # 今日の会話：まだ眠らない


def test_sleep_turns_yesterday_into_pages_and_leaves_today_in_hand(idea: Idea) -> None:
    _yesterday_and_today(idea)
    memory = _memory(idea)
    brain = Brain()
    report = _sleep(memory, brain, feelings=_felt_yesterday(idea))

    assert report.finished and report.episodes == 2 and report.written == 3 and report.failed == []
    pages = load_pages(idea.memory)
    episodes = [p for p in pages if p.kind == "episode"]
    diaries = [p for p in pages if p.kind == "diary"]
    assert [p.title for p in episodes] == ["海の約束", "仕事の話"]
    assert [p.concepts for p in episodes] == [("高野漁港",), ("仕事",)]
    assert all(p.written_by == "test-brain (2026-10-05)" and p.structured_by == p.written_by for p in episodes)
    assert episodes[0].next == episodes[1].id and episodes[1].prev == episodes[0].id
    assert len(diaries) == 1 and diaries[0].title == "話した日" and diaries[0].body.startswith("今日はマスター")
    assert diaries[0].concepts == ("高野漁港", "仕事")
    assert brain.calls.index("diary") > max(i for i, c in enumerate(brain.calls) if c == "episode")
    # 日記の材料は、その日の出来事と、そのときどきの本人の気持ちの言葉
    assert "海の約束" in brain.diary_prompts[0]
    assert "20:01 海の話が楽しい" in brain.diary_prompts[0] and "20:07 早く休んでほしい" in brain.diary_prompts[0]
    # ページの芯の数は、そのあいだの気持ちの記録から仕組みが決める（脳には書かせない）。心が動いた出来事ほど高ぶりが高い
    assert episodes[0].affect is not None and episodes[1].affect is not None and diaries[0].affect is not None
    assert episodes[0].arousal > episodes[1].arousal
    assert not episodes[0].feeling  # 新しいページは8軸を持たない
    # 今日の会話は、まだどのページにもなっていない
    left = unslept_lines(read_conversation(idea.conversation), pages, before=NOW + timedelta(days=1))
    assert [line.text for line in left] == ["おはよう", "おはよう！"]
    # 眠り終えたら索引ができていて、今日（10/5の朝7時から）は思い出せる
    assert set(memory.index.pages) == {p.id for p in pages}
    remembered = memory.recall(Cue("高野漁港の海、覚えてる？", (), NOW))
    assert remembered and remembered[0].page_id == episodes[0].id


def test_a_second_sleep_has_nothing_left_to_do(idea: Idea) -> None:
    _yesterday_and_today(idea)
    memory = _memory(idea)
    _sleep(memory, Brain())
    brain = Brain()
    report = _sleep(memory, brain)
    assert report.finished and report.episodes == 0 and report.written == 0 and brain.calls == []


def test_woken_sleep_continues_next_time_without_duplicates(idea: Idea) -> None:
    _yesterday_and_today(idea)
    memory = _memory(idea)
    brain = Brain()
    stop = {"now": False}

    def wake_after_structuring() -> bool:
        return stop["now"]

    brain.on_segment = lambda: stop.update(now=True)  # 区切り終えたところで起こされる
    first = _sleep(memory, brain, should_stop=wake_after_structuring)
    assert not first.finished and first.episodes == 2 and first.written == 0
    assert all(not p.written for p in load_pages(idea.memory))

    second = _sleep(memory, Brain())
    assert second.finished and second.episodes == 0 and second.written == 3
    pages = load_pages(idea.memory)
    assert len([p for p in pages if p.kind == "episode"]) == 2 and len([p for p in pages if p.kind == "diary"]) == 1
    assert all(p.written for p in pages)


def test_unwritten_pages_wait_for_the_next_sleep(idea: Idea) -> None:
    _yesterday_and_today(idea)
    memory = _memory(idea)
    brain = Brain()
    brain.fail_titles = {"仕事の話"}
    report = _sleep(memory, brain)
    assert report.finished and len(report.failed) == 1
    unwritten = [p for p in load_pages(idea.memory) if not p.written]
    assert [p.id for p in unwritten] == report.failed
    assert "まだ言葉にしていない出来事" in brain.diary_prompts[0]  # 日記は書けたページと記録から書く

    again = _sleep(memory, Brain())
    assert again.written == 1 and all(p.written for p in load_pages(idea.memory))


def test_structure_is_dropped_if_master_erased_the_record_meanwhile(idea: Idea) -> None:
    _yesterday_and_today(idea)
    memory = _memory(idea)
    brain = Brain()
    log = ConversationLog(idea.conversation)
    target = read_conversation(idea.conversation)[1]

    def erase() -> None:
        brain.on_segment = None
        log.remove(session=target.session, ts=target.ts.astimezone(timezone.utc).isoformat(), speaker=target.speaker, text=target.text)

    brain.on_segment = erase
    first = _sleep(memory, brain)
    assert first.episodes == 0  # 消された記録を含む区切りは使わない
    second = _sleep(memory, Brain())
    assert second.episodes >= 1
    pages = load_pages(idea.memory)
    assert unslept_lines(read_conversation(idea.conversation), pages, before=BEFORE) == []  # 残りは次の眠りでページになった
    assert target.text not in "\n".join(p.body for p in pages)


def test_old_conversations_already_in_memory_are_not_slept_again(idea: Idea) -> None:
    """最初の記憶（整理済みのページ）がある会話は、眠りの対象にならない。"""
    _talk(idea, _at("2026-08-04", "20:00"), "前の会話", "うん")
    lines = read_conversation(idea.conversation)
    from mind.core.memory.page import Page, write_page

    write_page(idea.memory, Page(
        id="ep-2026-08-04-01", kind="episode", start=lines[0].ts, end=lines[-1].ts,
        source=conversation_refs(lines), concepts=("前",), structured_by="claude",
    ).with_words(title="前の会話", gist="g", importance=5, written_by="serina"))
    _yesterday_and_today(idea)
    report = _sleep(_memory(idea), Brain())
    assert report.episodes == 2
    assert not any(p.kind == "diary" and p.start.date() == date(2026, 8, 4) for p in load_pages(idea.memory))


def test_diary_links_only_to_the_days_main_concepts() -> None:
    """日記は、その日の出来事によく出てきた概念から8つまでにつなぐ（何にでも反応するハブにしない）。"""
    from mind.core.memory.page import Page
    from mind.core.memory.sleep import diary_concepts

    at = _at("2026-10-04", "20:00")
    pages = [
        Page(id=f"ep-{n}", kind="episode", start=at, end=at, source=(), concepts=(f"c{n}", "海") + (("約束",) if n % 2 else ()))
        for n in range(10)
    ]
    concepts = diary_concepts(pages)
    assert concepts[:2] == ("海", "約束") and len(concepts) == 8
