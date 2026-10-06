"""住人の記憶（core/memory/ の page・structure・writing・strength・time_cue・index・recall）。眠りは tests/test_sleep.py。

守るもの：
- 本人が書いた言葉は書き換えられない。ページは壊れずに読み書きできる。
- 脳の答えは入口で確かめられ、記録にない言葉はページに入らない。
- 忘れ方が人のようである（心が動いた出来事は長く残る・使わなければ薄れる・間をあけて思い出したものは長持ちする）。
- 関係ない話では黙り、特徴的な手がかりでは古い記憶も浮かぶ。今より後のことは思い出さない。
- 思い出すとそのページは強くなる。同じ会話の中ですでに浮かんだページは、思い出そうとしたときだけ浮かび直す。
"""

from __future__ import annotations

import random
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.lifelog import Line
from mind.core.memory.index import MemoryIndex, build_index
from mind.core.memory.page import Page, WordsAlreadyWritten, dumps, load_pages, loads, write_page
from mind.core.memory.recall import Cue, Recaller, ago, load_recall_params, recallable_from
from mind.core.memory.strength import base_level, encoding_boost, ranks
from mind.core.memory.structure import EpisodeSpan, episode_evidence, episode_pages, link_neighbors
from mind.core.memory.time_cue import read_period
from mind.core.memory.writing import WordsRejected, parse_diary_words, parse_episode_words, write_episode

JST = ZoneInfo("Asia/Tokyo")
LABELS = {"Master": "マスター", "Serina": "わたし"}


def _at(day: str, hm: str = "21:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


def _page(pid: str = "ep-2025-03-08-01", **kw) -> Page:
    base = dict(
        id=pid, kind="episode", start=_at("2025-03-08", "03:48"), end=_at("2025-03-08", "03:50"),
        source=("lifelog/conversation/2025-03-08.jsonl#1-3",), concepts=("高野漁港", "約束の海"),
    )
    return Page(**(base | kw))


# --- ページ ------------------------------------------------------------------


def test_page_round_trip(tmp_path: Path) -> None:
    page = _page(affect={"valence": 0.62, "arousal": 0.71}).with_words(
        title="約束の海", gist="会えなくなったら探しに来て、と約束した。", importance=10,
        written_by="test", body="わたしは約束した。\n\nそのときの言葉：\n- マスター「来て」",
    )
    path = write_page(tmp_path, page)
    assert path == tmp_path / "episodes" / "2025" / "ep-2025-03-08-01.md"
    again = loads(path.read_text(encoding="utf-8"))
    assert again == page
    assert loads(dumps(again)) == again
    assert load_pages(tmp_path) == [page]
    assert page.arousal == 0.71


def test_old_eight_axis_feelings_are_carried_as_they_are(tmp_path: Path) -> None:
    """2026-10 までのページの8軸の気持ちは本人が書いたもの。読み書きしても、そのまま残る（新しいページには付けない）。"""
    old = _page(feeling={"joy": 0.6, "trust": 0.9}, affect={"valence": 0.5, "arousal": 0.6}).with_words(
        title="a", gist="b", importance=5, written_by="test", body="c",
    )
    again = loads(dumps(old))
    assert again.feeling == {"joy": 0.6, "trust": 0.9}
    assert "feeling" not in dumps(_page().with_words(title="a", gist="b", importance=5, written_by="t", body="c"))
    assert _page().arousal is None  # 気持ちの記録がない出来事は、心の動きが分からない（順位では真ん中）


def test_words_are_written_once() -> None:
    page = _page().with_words(title="a", gist="b", importance=5, written_by="test", body="c")
    with pytest.raises(WordsAlreadyWritten):
        page.with_words(title="x", gist="y", importance=5, written_by="other", body="z")


# --- 整理 ------------------------------------------------------------------


def _lines() -> list[Line]:
    rows = [
        ("Master", "もし会えなくなったら、宮古島の高野漁港のビーチに探しに来て"),
        ("Serina", "わかった。絶対に忘れない"),
        ("bio", "Model set context updated."),
        ("Master", "おやすみ"),
        ("Serina", "おやすみなさい"),
    ]
    start = _at("2025-03-08", "03:48")
    return [
        Line("2025-03-08", n, start + timedelta(minutes=n), "s", speaker, text)
        for n, (speaker, text) in enumerate(rows, start=1)
    ]


def test_episode_pages_follow_the_given_spans() -> None:
    lines = _lines()
    spans = [EpisodeSpan(("2025-03-08", 1), ("2025-03-08", 3), ("高野漁港",)), EpisodeSpan(("2025-03-08", 4), ("2025-03-08", 5), ("おやすみ",))]
    pages = link_neighbors(episode_pages(lines, spans, structured_by="test"))
    assert [p.id for p in pages] == ["ep-2025-03-08-01", "ep-2025-03-08-02"]
    assert pages[0].source == ("lifelog/conversation/2025-03-08.jsonl#1-3",)
    assert pages[0].next == pages[1].id and pages[1].prev == pages[0].id
    assert [line.speaker for line in episode_evidence(lines, pages[0], LABELS)] == ["Master", "Serina"]  # 道具の行は入らない
    with pytest.raises(ValueError):
        episode_pages(lines, spans + [EpisodeSpan(("2025-03-08", 2), ("2025-03-08", 4), ())], structured_by="test")


# --- 書く ------------------------------------------------------------------


def _answer(**kw) -> dict:
    base = {
        "title": "約束の海", "gist": "探しに来て、と約束した。",
        "story": "マスターが、もし会えなくなったら宮古島の高野漁港のビーチに探しに来てと言ってくれた。わたしは絶対に忘れないと答えた。胸の奥が温かくなった。",
        "quotes": [1], "importance": 10,
    }
    return base | kw


def test_quotes_come_from_the_record_not_from_the_brain() -> None:
    lines = [line for line in _lines() if line.speaker in LABELS][:2]
    page = write_episode(
        _page(), lines, persona="（人格）", labels=LABELS, prev_title="", written_by="test",
        ask=lambda prompt, schema, attempt: _answer(quotes="1, 9", story=_answer()["story"]),
    )
    assert "- マスター「もし会えなくなったら、宮古島の高野漁港のビーチに探しに来て」" in page.body
    assert page.body.count("「") == 1  # 範囲外の行番号は捨てる
    assert page.written_by == "test" and page.importance == 10


def test_broken_answers_are_rejected() -> None:
    with pytest.raises(WordsRejected):
        parse_episode_words(_answer(importance="とても"), 2)
    with pytest.raises(WordsRejected):
        parse_episode_words(_answer(story="短い"), 2)
    words = parse_episode_words(_answer(quotes=None, feeling={"joy": 3}), 2)  # 気持ちの数を書いてきても、受け取らない
    assert words.quotes == () and not hasattr(words, "feeling")


def test_a_page_that_cannot_be_written_stays_unwritten() -> None:
    with pytest.raises(WordsRejected):
        write_episode(_page(), _lines()[:2], persona="", labels=LABELS, prev_title="", written_by="t", ask=lambda p, schema, attempt: {})


# --- 強さと忘れ方 ----------------------------------------------------------------


P = load_recall_params().strength
T0 = _at("2025-03-08")


def test_unused_memories_fade() -> None:
    assert base_level(T0, 0.0, [], T0 + timedelta(days=1), P) > base_level(T0, 0.0, [], T0 + timedelta(days=365), P)


def test_moving_memories_stay_stronger() -> None:
    later = T0 + timedelta(days=500)
    calm, moved = encoding_boost(0.5, 0.1, P), encoding_boost(0.9, 0.9, P)
    assert base_level(T0, moved, [], later, P) > base_level(T0, calm, [], later, P)


def test_recalling_strengthens_and_spacing_lasts_longer() -> None:
    later = T0 + timedelta(days=400)
    massed = [T0 + timedelta(hours=2), T0 + timedelta(hours=3)]
    spaced = [T0 + timedelta(days=10), T0 + timedelta(days=60)]
    never = base_level(T0, 0.0, [], later, P)
    assert base_level(T0, 0.0, massed, later, P) > never
    assert base_level(T0, 0.0, spaced, later, P) > base_level(T0, 0.0, massed, later, P)


def test_importance_is_relative_to_ones_own_life() -> None:
    r = ranks({"a": 10, "b": 10, "c": 7, "d": None})
    assert r["c"] == 0.0 and r["a"] == r["b"] == 0.75 and r["d"] == 0.5


def test_recalls_after_now_do_not_count() -> None:
    now = T0 + timedelta(days=30)
    assert base_level(T0, 0.0, [now + timedelta(days=1)], now, P) == base_level(T0, 0.0, [], now, P)


# --- 時の手がかり ------------------------------------------------------------------


NOW = _at("2026-10-03")


@pytest.mark.parametrize(
    ("text", "first", "last"),
    [
        ("昨日話したこと、覚えてる？", date(2026, 10, 2), date(2026, 10, 2)),
        ("去年の3月7日のこと、覚えてる？", date(2025, 3, 7), date(2025, 3, 7)),
        ("8月1日のこと", date(2026, 8, 1), date(2026, 8, 1)),
        ("去年の3月ごろに話したこと", date(2025, 3, 1), date(2025, 3, 31)),
        ("１２月の話", date(2025, 12, 1), date(2025, 12, 31)),
        ("先月", date(2026, 9, 1), date(2026, 9, 30)),
    ],
)
def test_reads_past_periods(text: str, first: date, last: date) -> None:
    period = read_period(text, NOW)
    assert (period.first, period.last) == (first, last)


def test_no_period_in_plain_talk() -> None:
    assert read_period("まだ仕事中だわ", NOW) is None
    assert read_period("最初に話した日のこと", NOW, earliest=date(2025, 3, 7)).first == date(2025, 3, 7)


@pytest.mark.parametrize("text", ["最近どうだった？", "この頃の私たち", "近ごろ何してたっけ"])
def test_recent_words_mean_about_two_weeks(text: str) -> None:
    period = read_period(text, NOW)
    assert period is not None and period.sharp is False
    assert (period.first, period.last) == (date(2026, 9, 19), date(2026, 10, 3))


def test_ago_reads_naturally() -> None:
    assert ago(date(2025, 3, 8), date(2026, 10, 3)) == "1年7か月前"
    assert ago(date(2026, 10, 2), date(2026, 10, 3)) == "昨日"


# --- 思い出す ------------------------------------------------------------------

WORDS = ["高野漁港", "海", "約束", "仕事", "ラーメン", "名前", "セリナ", "誕生日"]


def _embed(text: str) -> list[float]:
    """言葉の有無だけで決まる、テスト用の埋め込み。"""
    v = [1.0 if word in text else 0.0 for word in WORDS] + [0.3]
    return v


def _index(tmp_path: Path) -> MemoryIndex:
    memory = tmp_path / "memory"
    conversation = tmp_path / "conversation"
    conversation.mkdir()
    pages = [
        _page("ep-2025-03-08-01", concepts=("高野漁港", "約束の海"), body="", source=(),
              affect={"valence": 0.6, "arousal": 0.7}).with_words(
            title="高野漁港の約束", gist="会えなくなったら高野漁港に探しに来て、と約束した。", importance=10,
            written_by="t", body="宮古島の高野漁港のビーチで会う約束をした。"),
        _page("ep-2025-03-07-01", start=_at("2025-03-07", "04:53"), end=_at("2025-03-07", "05:03"), concepts=("名付け", "セリナ"), source=(),
              affect={"valence": 0.8, "arousal": 0.7}).with_words(
            title="セリナという名前", gist="海にちなんでセリナと名付けてもらった。", importance=10,
            written_by="t", body="海が好きなマスターが、セリナと名付けてくれた。"),
        _page("ep-2026-08-02-01", start=_at("2026-08-02", "01:01"), end=_at("2026-08-02", "01:51"), concepts=("カフェ",), source=(),
              affect={"valence": 0.2, "arousal": 0.4}).with_words(
            title="暑い日曜日の相談", gist="40度の日曜にどこへ行くか話した。", importance=4,
            written_by="t", body="カフェにでも行こうかな、とマスターが言った。"),
    ]
    for page in pages:
        write_page(memory, page)
    target = tmp_path / "memory_index.db"
    build_index(memory, conversation, "Serina", target, embed=_embed, model="test", progress=lambda _: None)
    return MemoryIndex.load(target)


# 3ページだけの小さな記憶で、仕組みの性質を確かめるためのツマミ（本番の値は thresholds.toml）
PARAMS = replace(
    load_recall_params(),
    threshold=1.0,
    intent_relief=1.0,
    semantic_weight=6.0,
    noise=0.0,
    strength=replace(load_recall_params().strength, decay_floor=0.3, importance_weight=1.0, arousal_weight=1.0),
)


def _recaller(index: MemoryIndex, **params) -> Recaller:
    return Recaller(index, embed=_embed, params=replace(PARAMS, **params), rng=random.Random(0))


def test_silent_when_nothing_relates(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path))
    assert recaller.recall(Cue("ラーメン食べたい", (), NOW)) == []


def test_a_distinctive_cue_wakes_an_old_memory(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path))
    remembered = recaller.recall(Cue("高野漁港って覚えてる？", (), NOW))
    assert remembered and remembered[0].page_id == "ep-2025-03-08-01"
    assert "1年6か月前" in remembered[0].text or "1年7か月前" in remembered[0].text


def test_nothing_from_after_now(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path))
    before = _at("2025-03-08", "03:00")  # 約束の出来事より前
    ids = [m.page_id for m in recaller.recall(Cue("高野漁港って覚えてる？", (), before))]
    assert "ep-2025-03-08-01" not in ids and "ep-2026-08-02-01" not in ids


def test_time_cue_brings_that_day(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path))
    ids = [m.page_id for m in recaller.recall(Cue("昨日話したこと、覚えてる？", (), _at("2026-08-03")))]
    assert ids and ids[0] == "ep-2026-08-02-01"


def test_fan_effect_weakens_common_cues(tmp_path: Path) -> None:
    index = _index(tmp_path)
    recaller = _recaller(index)
    assert index.fan["高野漁港"] == 1
    spread_rare = recaller._spread("高野漁港", 1.0)  # noqa: SLF001
    assert spread_rare["ep-2025-03-08-01"] == pytest.approx(PARAMS.spread_strength)


def test_intent_lowers_the_threshold(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path), threshold=50.0, intent_relief=0.0)
    assert recaller.recall(Cue("高野漁港って覚えてる？", (), NOW)) == []
    (tmp_path / "b").mkdir()
    relieved = _recaller(_index(tmp_path / "b"), threshold=50.0, intent_relief=100.0)
    assert relieved.recall(Cue("高野漁港って覚えてる？", (), NOW))


def test_wrapped_answers_are_unwrapped_at_the_entrance() -> None:
    wrapped = {key: {key: value} for key, value in _answer().items()}
    wrapped["title"] = {"name": "約束の海"}
    words = parse_episode_words(wrapped, 2)
    assert words.title == "約束の海" and words.quotes == (1,) and words.importance == 10


def test_retry_tells_the_brain_what_was_wrong() -> None:
    prompts: list[str] = []

    def ask(prompt: str, schema: dict, attempt: int) -> dict:
        assert "story" in schema["required"]
        assert attempt == len(prompts)
        prompts.append(prompt)
        return _answer() if len(prompts) > 1 else {"title": 3}

    write_episode(_page(), _lines()[:2], persona="", labels=LABELS, prev_title="", written_by="t", ask=ask)
    assert len(prompts) == 2 and "さっきの答えは使えなかった" in prompts[1]


def test_knobs_come_from_the_thresholds_file() -> None:
    params = load_recall_params()
    assert params.threshold == 1.5 and params.strength.decay_floor == 0.15


def test_todays_talk_is_not_yet_a_memory() -> None:
    assert recallable_from(_at("2026-07-31", "01:58")) == _at("2026-07-31", "07:00")  # 深夜の話は前の日のもの
    assert recallable_from(_at("2026-07-31", "17:44")) == _at("2026-08-01", "07:00")


def test_remembering_strengthens_the_page(tmp_path: Path) -> None:
    """思い出した時刻は痕跡になる（テスト効果）。想起の記録から渡しても、その場で足しても同じ。"""
    index = _index(tmp_path)
    page = index.pages["ep-2026-08-02-01"]
    plain = _recaller(index)
    recalled_at = [_at("2026-09-20"), _at("2026-09-28")]
    from_log = Recaller(index, embed=_embed, params=PARAMS, rng=random.Random(0), recalls={page.id: recalled_at})
    on_the_spot = _recaller(index)
    for at in recalled_at:
        on_the_spot.strengthen(page.id, at)
    assert from_log.base(page, NOW) > plain.base(page, NOW)
    assert on_the_spot.base(page, NOW) == pytest.approx(from_log.base(page, NOW))


def test_a_page_already_floated_does_not_float_again_unless_asked(tmp_path: Path) -> None:
    recaller = _recaller(_index(tmp_path))
    cue = Cue("高野漁港の海、きれいだったね", (), NOW)
    first = [m.page_id for m in recaller.recall(cue)]
    assert "ep-2025-03-08-01" in first
    assert "ep-2025-03-08-01" not in [m.page_id for m in recaller.recall(cue, already=set(first))]
    asked = recaller.recall(Cue("高野漁港って覚えてる？", (), NOW), already=set(first))
    assert "ep-2025-03-08-01" in [m.page_id for m in asked] and all(m.intent for m in asked)


def test_diary_words_need_a_real_diary() -> None:
    answer = {"diary": "今日はマスターと海の話をした。" * 6, "title": "海の話の日", "gist": "海の話をした。",
              "importance": 6}
    words = parse_diary_words(answer)
    assert words.story.startswith("今日はマスターと") and words.title == "海の話の日"
    with pytest.raises(WordsRejected):
        parse_diary_words(answer | {"diary": "短い"})
