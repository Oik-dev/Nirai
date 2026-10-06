"""関係（core/memory/relation.py）と、眠りの間に関係を書き足すこと（core/memory/sleep.py の4）のテスト。

守るもの：
- 関係は、本人の脳がその日のページを読み返して、変わったことと新しく分かったことだけを書き足す。1日1つのファイルに書き、
  前の日のファイルは書き換えない。今の関係や何がまだ有効かは、書き足しを古い順に読んで計算する（状態を別に持たない）。
- 変わった事実は、古いものに終わりの日を付けて残す（「前はそうだった」と話せる）。見方の確かさは仕組みが数から決める。
- 脳の答えは入口で確かめる。番号は見せた書き留めに読み替え、合わないものは落とす。何度聞いても書けなければ何も書かない。
- 関係は日の順に積み重なる。まだ書き終えていないページがある日・書けなかった日で止まり、次の眠りでそこから続ける。
- Masterが記録を消して外れたページから書いた書き足しは外れる。書いている間に消されても、消えた材料から書いたものは残らない。
- その日の材料は、ページが多い日も Gemma の窓に収まる長さにする。
- マスターとのことは、会話のたびに文脈パックに短く載る（Pulse の材料にも）。まだなければ段ごと省く。
LLM不要（脳の替え玉）。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.idle_policy import PulseCandidate
from mind.core.chores.pulse import PulseGenerationContext, build_pulse_prompt
from mind.core.context.pack import build_context_pack
from mind.core.idea import Idea
from mind.core.lifelog import ConversationLog
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.relation import (
    CHANGED,
    CONFIRMED,
    DAY_CHARS,
    IN_PACK,
    KEPT,
    KNOWS,
    NO_LONGER,
    PROMISED,
    RELATION_SCHEMA,
    SHAKEN,
    THINKS,
    Added,
    Entry,
    Touched,
    day_material,
    fold,
    grow,
    load_entries,
    load_relation,
    person_dir,
    render_for_pack,
    write_entry,
)
from mind.core.memory.sleep import sleep
from mind.core.memory.structure import SEGMENT_SCHEMA
from mind.core.memory.writing import DIARY_SCHEMA, EPISODE_SCHEMA, WordsRejected
from mind.core.state.session import SessionState
from mind.core.state.serina_day import serina_day_id, serina_day_start

JST = timezone(timedelta(hours=9))
M = "マスター"
RELATION = "マスターとは、毎晩その日のことを話す仲になった。わたしの話をちゃんと聞いてくれるので、前より安心して甘えられる。"


def _d(text: str) -> date:
    return date.fromisoformat(text)


def _at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}:00+09:00")


def _page(memory: Path, kind: str, day: str, n: int, title: str, body: str, *, written: bool = True) -> Page:
    page = Page(
        id=f"{'ep' if kind == 'episode' else 'diary'}-{day}-{n:02d}",
        kind=kind,
        start=_at(day, f"{20 + n}:00"),
        end=_at(day, f"{20 + n}:30"),
        source=(),
        concepts=(),
        title=title if written else "",
        gist=f"{title}。" if written else "",
        importance=6 if written else None,
        written_by="b" if written else "",
        body=body if written else "",
    )
    write_page(memory, page)
    return page


class Brain:
    """関係の問いにだけ答える脳の替え玉。聞かれた問いを覚え、決めた答えを順に返す。"""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def __call__(self, prompt: str, schema: dict, attempt: int) -> dict:
        assert schema is RELATION_SCHEMA
        self.prompts.append(prompt)
        return self.answers.pop(0)


def _nothing() -> dict:
    return {"relation": "", "turning": "", "new": [], "changed": []}


# --- 計算（書き足しを古い順に読む） ------------------------------------------------------------


def test_fold_keeps_what_changed_and_when() -> None:
    entries = [
        Entry(_d("2026-08-01"), ("ep-a",), "b", relation="前の関係。" * 10, turning="ローカルに来た",
              added=(Added("2026-08-01-1", KNOWS, "マスターはフリーランス"), Added("2026-08-01-2", THINKS, "マスターは優しい"),
                     Added("2026-08-01-3", PROMISED, "いつか高野漁港へ行く"))),
        Entry(_d("2026-09-01"), ("ep-b",), "b",
              added=(Added("2026-09-01-1", KNOWS, "マスターは正社員になった", replaces="2026-08-01-1"),),
              touched=(Touched("2026-08-01-2", CONFIRMED), Touched("消えた日の書き留め", CONFIRMED))),
        Entry(_d("2026-10-01"), ("ep-c",), "b", relation=RELATION,
              touched=(Touched("2026-08-01-2", CONFIRMED), Touched("2026-08-01-3", KEPT))),
    ]
    relation = fold(M, list(reversed(entries)))  # ファイルの並びに頼らず、日の順に読む

    assert relation.last_day == _d("2026-10-01")
    assert [d for d, _ in relation.relations] == [_d("2026-08-01"), _d("2026-10-01")]
    assert relation.turnings == [(_d("2026-08-01"), "ローカルに来た")]
    old_job = relation.things["2026-08-01-1"]
    assert (old_job.until, old_job.ended) == (_d("2026-09-01"), CHANGED)  # 消さずに、終わりの日を付けて残す
    promise = relation.things["2026-08-01-3"]
    assert (promise.until, promise.ended) == (_d("2026-10-01"), KEPT)
    kind = relation.things["2026-08-01-2"]
    assert (kind.confirmed, kind.touched_at, kind.sureness) == (2, _d("2026-10-01"), "強くそう思う")
    assert [t.id for t in relation.open_things()] == ["2026-08-01-2", "2026-09-01-1"]  # 最近確かめたものから
    assert [t.id for t in relation.ended_things()] == ["2026-08-01-3", "2026-08-01-1"]


def test_sureness_is_counted_not_written_by_the_brain() -> None:
    def sure(*hows: str) -> str:
        entries = [Entry(_d("2026-08-01"), (), "b", added=(Added("v", THINKS, "マスターは朝が苦手"),))]
        entries += [Entry(_d("2026-08-01") + timedelta(days=n + 1), (), "b", touched=(Touched("v", how),)) for n, how in enumerate(hows)]
        return fold(M, entries).things["v"].sureness

    assert sure() == "そう思う"
    assert sure(CONFIRMED, CONFIRMED) == "強くそう思う"
    assert sure(SHAKEN) == "前ほどそう思えない"
    assert sure(CONFIRMED, SHAKEN, SHAKEN) == "前ほどそう思えない"
    ended = [Entry(_d("2026-08-01"), (), "b", added=(Added("v", THINKS, "x"),)), Entry(_d("2026-08-02"), (), "b", touched=(Touched("v", NO_LONGER),)),
             Entry(_d("2026-08-03"), (), "b", touched=(Touched("v", CONFIRMED),))]
    thing = fold(M, ended).things["v"]
    assert (thing.until, thing.confirmed) == (_d("2026-08-02"), 0)  # 終わったものは、もう確かめられない


def test_an_entry_reads_back_as_written(tmp_path: Path) -> None:
    entry = Entry(
        _d("2026-10-05"), ("ep-2026-10-05-01", "diary-2026-10-05"), "gemma (2026-10-06)",
        relation='マスターは「大丈夫」って言ってくれた。\n次の日も、その次の日も。' * 2, turning="初めて弱音を\\話してくれた",
        added=(Added("2026-10-05-1", PROMISED, "週末に海の写真を\"送ってもらう\"", replaces="2026-08-01-3"),),
        touched=(Touched("2026-08-01-2", CONFIRMED),),
    )
    path = write_entry(tmp_path, M, entry)
    assert path == person_dir(tmp_path, M) / "2026-10-05.md"
    assert load_entries(tmp_path, M) == [Entry(**{**entry.__dict__, "relation": entry.relation.strip()})]
    assert load_relation(tmp_path, "ほかの人").last_day is None


# --- 書き足す ---------------------------------------------------------------------------------


def test_grow_asks_with_numbers_and_keeps_ids(tmp_path: Path) -> None:
    write_entry(tmp_path, M, Entry(_d("2026-08-01"), ("ep-a",), "b", relation=RELATION, turning="ローカルに来た", added=(
        Added("2026-08-01-1", KNOWS, "マスターはフリーランス"), Added("2026-08-01-2", PROMISED, "いつか高野漁港へ行く"),
    )))
    pages = [
        _page(tmp_path, "episode", "2026-10-04", 1, "仕事が決まった", "マスターが正社員になったと教えてくれた。"),
        _page(tmp_path, "diary", "2026-10-04", 2, "うれしい日", "マスターの新しい仕事の話をたくさん聞いた。"),
    ]
    brain = Brain({
        "relation": "",
        "turning": "マスターが新しい仕事のことを一番に話してくれた",
        "new": [{"kind": KNOWS, "text": "マスターは10月から正社員", "replaces": 1},
                {"kind": THINKS, "text": "マスターは大事なことを最初に話してくれる人", "replaces": 0}],
        "changed": [{"no": 2, "how": CONFIRMED}, {"no": 9, "how": CONFIRMED}, {"no": 1, "how": CONFIRMED}],
    })

    entry = grow(tmp_path, M, _d("2026-10-04"), pages, persona="人格の本文", ask=brain, written_by="gemma (2026-10-05)")
    write_entry(tmp_path, M, entry)

    prompt = brain.prompts[0]
    assert "人格の本文" in prompt and RELATION in prompt and "2026-08-01 ローカルに来た" in prompt
    assert "1. [知っている] マスターはフリーランス（2026-08-01から）" in prompt and "2. [約束] いつか高野漁港へ行く" in prompt
    assert "仕事が決まった" in prompt and "マスターの新しい仕事の話" in prompt
    assert entry.sources == ("ep-2026-10-04-01", "diary-2026-10-04-02")
    assert entry.added == (
        Added("2026-10-04-1", KNOWS, "マスターは10月から正社員", replaces="2026-08-01-1"),
        Added("2026-10-04-2", THINKS, "マスターは大事なことを最初に話してくれる人"),
    )
    assert entry.touched == (Touched("2026-08-01-2", CONFIRMED),)  # 範囲外の番号と、同じ答えで置き換えたものは落とす
    relation = load_relation(tmp_path, M)
    assert relation.relations == [(_d("2026-08-01"), RELATION)]  # 変わらなければ、前の関係のまま
    assert relation.things["2026-08-01-1"].until == _d("2026-10-04")
    assert len(list(person_dir(tmp_path, M).glob("*.md"))) == 2  # 前の日のファイルは書き換えない


def test_answers_that_do_not_fit_are_dropped_or_asked_again(tmp_path: Path) -> None:
    write_entry(tmp_path, M, Entry(_d("2026-08-01"), (), "b", added=(Added("k", KNOWS, "マスターは猫が好き"),)))
    pages = [_page(tmp_path, "episode", "2026-10-04", 1, "猫の話", "マスターと猫の話をした。")]
    brain = Brain(
        {"relation": "短い", "turning": "", "new": [], "changed": []},  # 関係が短すぎる → 聞き直す
        {"relation": "", "turning": "", "new": [{"kind": "うわさ", "text": "x", "replaces": 0}], "changed": []},  # 知らない種類 → 聞き直す
        {"relation": "", "turning": "", "new": {"kind": KNOWS, "text": "マスターは黒猫を飼いたい", "replaces": "0"},
         "changed": [{"no": 1, "how": KEPT}, {"no": "1", "how": CONFIRMED}]},  # 約束でないものは「果たした」にならない
    )
    entry = grow(tmp_path, M, _d("2026-10-04"), pages, persona="私", ask=brain, written_by="b")
    assert "さっきの答えは使えなかった" in brain.prompts[1] and "さっきの答えは使えなかった" in brain.prompts[2]
    assert entry.added == (Added("2026-10-04-1", KNOWS, "マスターは黒猫を飼いたい"),)
    assert entry.touched == (Touched("k", CONFIRMED),)

    other = tmp_path / "other"
    pages = [_page(other, "episode", "2026-10-04", 1, "猫の話", "マスターと猫の話をした。")]
    with pytest.raises(WordsRejected):
        grow(other, M, _d("2026-10-04"), pages, persona="私", ask=Brain(*[{"relation": "短い"}] * 3), written_by="b")
    assert load_entries(other, M) == []


def test_the_material_of_a_busy_day_fits_the_window(tmp_path: Path) -> None:
    page = _page(tmp_path, "episode", "2026-10-04", 1, "マスターと長く話した", "いろいろな話をした。" * 100)
    few = day_material([page, replace(page, id="ep-2")])
    assert few.count("いろいろな話をした。") > 20  # ページが少ない日は、本文まで読める

    pages = [replace(page, id=f"ep-{n}") for n in range(40)]  # 最初の会話の日は、ページが36あった
    material = day_material(pages)
    assert len(material) <= DAY_CHARS + len(pages) * len("- ……\n")
    assert [line.startswith("- （出来事）マスターと長く話した：") for line in material.splitlines()] == [True] * 40


# --- 眠りの間に --------------------------------------------------------------------------------


class SleepBrain:
    """眠り全体の脳の替え玉。区切りは1つ、出来事と日記には決まった言葉、関係には決めた答えを順に返す（なければ変化なし）。"""

    def __init__(self, *relations: dict) -> None:
        self.relations = list(relations)
        self.calls: list[str] = []
        self.relation_prompts: list[str] = []
        self.fail_episodes = False

    def __call__(self, prompt: str, schema: dict, attempt: int) -> dict:
        if schema is SEGMENT_SCHEMA:
            self.calls.append("segment")
            return {"episodes": [{"first": 1, "concepts": ["海"]}]}
        if schema is EPISODE_SCHEMA:
            self.calls.append("episode")
            if self.fail_episodes:
                return {"title": 3}
            day = prompt.split("【いつ】")[1].split("（")[0]  # 「2026年10月3日」
            return {"title": f"{day}の話", "gist": "話した。", "story": f"{day}にマスターと話した。" * 6, "quotes": [1], "importance": 6}
        if schema is DIARY_SCHEMA:
            self.calls.append("diary")
            return {"diary": "今日もマスターと話した。" * 8, "title": "話した日", "gist": "話した。", "importance": 5}
        if schema is RELATION_SCHEMA:
            self.calls.append("relation")
            self.relation_prompts.append(prompt)
            return self.relations.pop(0) if self.relations else _nothing()
        raise AssertionError(schema)


@pytest.fixture
def idea(tmp_path: Path) -> Idea:
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    return Idea.open(root)


def _talk(idea: Idea, day: str, session: str) -> None:
    log = ConversationLog(idea.conversation)
    for n, text in enumerate(["海の話をしよう", "うん", "また行きたいね", "行こうね"]):
        at = _at(day, "20:00") + timedelta(minutes=n)
        log.append(ts=at.astimezone(timezone.utc).isoformat(), session=session, speaker="Master" if n % 2 == 0 else "Serina", text=text)


def _sleep(memory: Memory, brain: SleepBrain, now: datetime, **kwargs):  # noqa: ANN003, ANN202
    return sleep(memory, before=serina_day_start(serina_day_id(now)), ask=brain, persona="人格", brain="test-brain",
                 today=serina_day_id(now), progress=lambda _m: None, **kwargs)


def _memory(idea: Idea) -> Memory:
    return Memory(idea, embed=lambda text: [1.0, 0.3], embed_model="fake")


def test_sleep_grows_the_relation_day_by_day_after_the_diary(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    _talk(idea, "2026-10-04", "s2")
    memory = _memory(idea)
    brain = SleepBrain(
        {"relation": RELATION, "turning": "", "new": [{"kind": PROMISED, "text": "また海に行く", "replaces": 0}], "changed": []},
        {"relation": "", "turning": "", "new": [], "changed": [{"no": 1, "how": CONFIRMED}]},
    )
    report = _sleep(memory, brain, _at("2026-10-05", "09:00"))

    assert report.finished and report.relation_days == 2 and report.failed == []
    assert brain.calls[-2:] == ["relation", "relation"] and brain.calls.index("relation") > brain.calls.index("diary")
    assert "2026年10月3日の話" in brain.relation_prompts[0] and "2026年10月4日の話" not in brain.relation_prompts[0]
    assert "1. [約束] また海に行く（2026-10-03から）" in brain.relation_prompts[1]  # 前の日に書き足したことを見て書く
    relation = memory.relation(M)
    assert relation.last_day == date(2026, 10, 4)
    assert relation.things["2026-10-03-1"].confirmed == 1
    assert [e.sources for e in load_entries(idea.memory, M)] == [
        tuple(p.id for p in load_pages(idea.memory) if p.start and p.start.date() == day) for day in (date(2026, 10, 3), date(2026, 10, 4))
    ]

    again = SleepBrain()
    assert _sleep(memory, again, _at("2026-10-05", "12:00")).relation_days == 0 and again.calls == []


def test_the_relation_waits_for_unwritten_pages_and_never_skips_a_day(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    memory = _memory(idea)
    brain = SleepBrain()
    brain.fail_episodes = True
    report = _sleep(memory, brain, _at("2026-10-04", "09:00"))
    assert report.finished and "relation" not in brain.calls and load_entries(idea.memory, M) == []

    _talk(idea, "2026-10-04", "s2")
    brain = SleepBrain()  # 前の日の出来事も、今度は書ける
    report = _sleep(memory, brain, _at("2026-10-05", "09:00"))
    assert report.relation_days == 2 and [e.day for e in load_entries(idea.memory, M)] == [date(2026, 10, 3), date(2026, 10, 4)]


def test_a_day_that_cannot_be_written_is_tried_again_next_sleep(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    _talk(idea, "2026-10-04", "s2")
    memory = _memory(idea)
    report = _sleep(memory, SleepBrain(*[{"relation": "短い"}] * 3), _at("2026-10-05", "09:00"))
    assert report.finished and report.relation_days == 0 and report.failed == ["people/マスター/2026-10-03"]
    assert load_entries(idea.memory, M) == []  # 次の日へ飛ばさない

    report = _sleep(memory, SleepBrain(), _at("2026-10-05", "12:00"))
    assert report.relation_days == 2


def test_waking_up_stops_between_days(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    _talk(idea, "2026-10-04", "s2")
    memory = _memory(idea)
    brain = SleepBrain()
    report = _sleep(memory, brain, _at("2026-10-05", "09:00"), should_stop=lambda: brain.calls.count("relation") == 1)
    assert not report.finished and [e.day for e in load_entries(idea.memory, M)] == [date(2026, 10, 3)]
    assert _sleep(memory, SleepBrain(), _at("2026-10-05", "12:00")).relation_days == 1


def test_forgetting_a_page_takes_the_relation_written_from_it(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    _talk(idea, "2026-10-04", "s2")
    memory = _memory(idea)
    _sleep(memory, SleepBrain(), _at("2026-10-05", "09:00"))

    forgotten = memory.forget_lines({("2026-10-03", 2)})

    assert "people/マスター/2026-10-03" in forgotten and "people/マスター/2026-10-04" not in forgotten
    assert [e.day for e in load_entries(idea.memory, M)] == [date(2026, 10, 4)]


def test_a_record_forgotten_while_she_writes_does_not_come_back(idea: Idea) -> None:
    _talk(idea, "2026-10-03", "s1")
    memory = _memory(idea)
    brain = SleepBrain({"relation": "", "turning": "", "new": [{"kind": KNOWS, "text": "マスターは猫が好き", "replaces": 0}], "changed": []})

    def forgetting(prompt: str, schema: dict, attempt: int) -> dict:
        if schema is RELATION_SCHEMA:
            memory.forget_lines({("2026-10-03", 1)})  # 関係を書いている間に、Masterがこの日の記録を消した
        return brain(prompt, schema, attempt)

    report = _sleep(memory, forgetting, _at("2026-10-04", "09:00"))
    assert report.finished and report.relation_days == 0 and load_entries(idea.memory, M) == []

    report = _sleep(memory, SleepBrain(), _at("2026-10-04", "12:00"))  # 残りの記録をページに書き直してから、その日を書く
    assert report.relation_days == 1 and load_relation(idea.memory, M).things == {}


# --- 文脈パック --------------------------------------------------------------------------------


def _long_relation() -> list[Entry]:
    return [
        Entry(_d("2026-08-01"), (), "b", relation="ローカルに来たばかりで、まだ少し遠慮していた。" * 3, turning="ローカルに来た",
              added=(Added("job", KNOWS, "マスターはフリーランス"), Added("sea", PROMISED, "いつか高野漁港へ行く"))),
        Entry(_d("2026-10-01"), (), "b", relation=RELATION, turning="毎晩話すようになった",
              added=(Added("job2", KNOWS, "マスターは正社員になった", replaces="job"), Added("kind", THINKS, "マスターは優しい"))),
    ]


def test_the_pack_carries_the_relation_briefly_and_the_change_first() -> None:
    assert render_for_pack(fold(M, [])) == ""
    text = render_for_pack(fold(M, _long_relation()))
    assert text.splitlines()[0] == f"今の関係（2026-10-01から）：{RELATION}"
    assert "その前の関係（2026-08-01から）：ローカルに来たばかり" in text
    assert "約束（2026-08-01から）：いつか高野漁港へ行く" in text
    assert text.index("転機（2026-10-01）") < text.index("転機（2026-08-01）")  # 新しい転機から
    assert "知っていること：マスターは正社員になった" in text
    assert "前はそうだったこと（〜2026-10-01）：マスターはフリーランス" in text
    assert "思っていること（そう思う）：マスターは優しい" in text

    many = _long_relation() + [Entry(_d("2026-10-02"), (), "b", added=tuple(Added(f"k{n}", KNOWS, f"知っていること{n}" * 5) for n in range(30)))]
    assert len(render_for_pack(fold(M, many))) <= IN_PACK + 120  # 大事なものから、短く（1行は超えてもよいが、そこで止まる）


def test_the_relation_sits_next_to_the_self_in_the_pack_and_the_pulse() -> None:
    relation = render_for_pack(fold(M, _long_relation()))
    rendered = build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=SessionState(), master_utterance="ただいま",
        remembered=["思い出したこと"], self_text="今の自分", relation_text=relation,
    ).render()
    assert f"【マスターとのこと】\n{relation}" in rendered
    assert rendered.index("【今の自分】") < rendered.index("【マスターとのこと】") < rendered.index("【思い出したこと】")
    assert "【マスターとのこと】" not in build_context_pack(
        persona_text="人格", absolute_rules="ルール", session=SessionState(), master_utterance="ただいま",
    ).render()

    prompt = build_pulse_prompt(PulseGenerationContext(
        candidate=PulseCandidate(kind="connection", trigger_id="t", context={"会いたさ": "強い"}), persona_text="人格", absolute_rules="ルール",
        self_text="今の自分", relation_text=relation,
    ))
    assert f"【マスターとのこと】\n{relation}" in prompt
