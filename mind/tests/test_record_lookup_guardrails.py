"""段2の保護検査。全例、使い捨てのイデアと偽の脳だけで確認する。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from mind.core.config import ThresholdsConfig
from mind.core.idea import Idea
from mind.core.lifelog import MASTER, ConversationLog
from mind.core.memory.index import build_index
from mind.core.memory.lookup import Record, RecordLookup
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, write_page
from mind.core.memory.recall import Cue, load_recall_params
from mind.core.routing.home_marks import HomeMarks
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

NOW = datetime(2026, 10, 12, 7, tzinfo=timezone.utc)
THEN = datetime(2026, 10, 10, 7, tzinfo=timezone.utc)


def _embed(text: str) -> list[float]:
    return [float("高野漁港" in text), float("海辺" in text), 1.0]


def _setup_memory(tmp_path, *, page=False):
    idea = Idea(tmp_path)
    (idea.root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    log = ConversationLog(idea.conversation)
    log.append(ts=THEN.isoformat(), speaker=MASTER, text="高野漁港で話そう。")
    log.append(ts=THEN.isoformat(), speaker="Serina", text="高野漁港の話を覚えているよ。")
    log.append(ts=NOW.isoformat(), speaker=MASTER, text="海辺でお茶を飲んだ。")
    log.append(ts=NOW.isoformat(), speaker="Serina", text="海辺にはまた行きたい。")
    if page:
        write_page(idea.memory, Page(
            id="ep-past", kind="episode", start=THEN, end=THEN,
            source=("lifelog/conversation/2026-10-10.jsonl#1-2",), concepts=("高野漁港",)
        ).with_words(title="高野漁港で話したこと", gist="港のこと", importance=7,
                     written_by="test", body="港の話をした。"))
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=_embed, model="test", progress=lambda _: None)
    return idea


def _recaller(idea, *, threshold=100.0):
    params = replace(load_recall_params(), threshold=threshold, intent_relief=0, noise=0)
    return Memory(idea, embed=_embed, embed_model="test", params=params)


def test_deleted_records_never_escape_index_or_live_today(tmp_path):
    idea = _setup_memory(tmp_path)
    memory = _recaller(idea)
    lookup = RecordLookup(idea.memory_index, idea.conversation)
    cue = Cue("海辺って覚えてる？", (), NOW)
    assert any("お茶" in row.text for row in lookup.search(cue, memory._recaller, limit=5)[0])

    ConversationLog(idea.conversation).remove_at("2026-10-12", 1)
    # まだ索引に残る間であっても、正本のdeletedを見て除外する。
    assert all("お茶" not in row.text for row in lookup.search(cue, memory._recaller, limit=5)[0])
    # まだページになっていない行も、Masterの削除経路から索引を作り直す。
    assert memory.forget_lines({("2026-10-12", 1)}) == []
    assert all("お茶" not in row.text for row in lookup.search(cue, memory._recaller, limit=5)[0])

    # 同じ日に追記した、まだ索引に存在しない記録も直接引ける。ただしdeletedは引かない。
    ConversationLog(idea.conversation).append(ts=NOW.isoformat(), speaker="master", text="海辺で新しい約束。")
    assert any("新しい約束" in row.text for row in lookup.search(cue, memory._recaller, limit=5)[0])
    ConversationLog(idea.conversation).remove_at("2026-10-12", 3)
    assert all("新しい約束" not in row.text for row in lookup.search(cue, memory._recaller, limit=5)[0])


def test_two_character_like_and_one_character_not_queried(tmp_path):
    idea = _setup_memory(tmp_path)
    memory = _recaller(idea)
    lookup = RecordLookup(idea.memory_index, idea.conversation)
    assert lookup.search(Cue("海辺って覚えてる？", (), NOW), memory._recaller, limit=5)[0]
    assert lookup.search(Cue("海って覚えてる？", (), NOW), memory._recaller, limit=5)[0] == []


def test_master_speaker_is_preserved_from_real_lifelog_format(tmp_path):
    idea = _setup_memory(tmp_path)
    memory = _recaller(idea)
    records, _ = RecordLookup(idea.memory_index, idea.conversation).search(
        Cue("高野漁港って覚えてる？", (), NOW), memory._recaller, limit=5
    )
    assert any("マスター:\n高野漁港で話そう。" in row.format() for row in records)
    assert all("セリナ:\n高野漁港で話そう。" not in row.format() for row in records)


def test_old_and_new_records_match_normalized_names_without_mutating_original(tmp_path):
    idea = _setup_memory(tmp_path)
    log = ConversationLog(idea.conversation)
    log.append(ts=THEN.isoformat(), speaker=MASTER, text="Unreal Engineの話をした。")
    log.append(ts=THEN.isoformat(), speaker=MASTER, text="ＦＦＸＩＶの話をした。")
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=_embed, model="test", progress=lambda _: None)
    memory = _recaller(idea)
    search = RecordLookup(idea.memory_index, idea.conversation)
    for question, original in [
        ("Unreal Engineって覚えてる？", "Unreal Engineの話をした。"),
        ("FFXIVって覚えてる？", "ＦＦＸＩＶの話をした。"),
    ]:
        found, _ = search.search(Cue(question, (), NOW), memory._recaller, limit=6)
        assert any(record.text == original for record in found), question


def test_recall_only_question_uses_recent_topic(tmp_path):
    idea = _setup_memory(tmp_path)
    memory = _recaller(idea)
    cue = Cue("覚えてる？", ((MASTER, "高野漁港の話だよ"), ("Serina", "うん")), NOW)
    found, _ = RecordLookup(idea.memory_index, idea.conversation).search(
        cue, memory._recaller, limit=5
    )
    assert any("高野漁港" in row.text for row in found)


def test_long_related_source_beats_newer_unrelated_lines(tmp_path):
    idea = Idea(tmp_path)
    (idea.root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    log = ConversationLog(idea.conversation)
    long_text = "高野漁港で大切な約束をした。" + "詳しい記録" * 110
    log.append(ts=THEN.isoformat(), speaker=MASTER, text=long_text)
    log.append(ts=THEN.isoformat(), speaker=MASTER, text="今夜は眠い。")
    log.append(ts=THEN.isoformat(), speaker="Serina", text="夕飯を食べた。")
    write_page(idea.memory, Page(
        id="ep-long", kind="episode", start=THEN, end=THEN,
        source=("lifelog/conversation/2026-10-10.jsonl#1-3",),
        concepts=("高野漁港",)
    ).with_words(title="約束", gist="高野漁港", importance=8,
                 written_by="test", body="海で話した。"))
    build_index(idea.memory, idea.conversation, "Serina", idea.memory_index,
                embed=_embed, model="test", progress=lambda _: None)
    memory = _recaller(idea)
    cue = Cue("高野漁港って覚えてる？", (), NOW)
    memory.recall(cue)  # 既存の埋め込みを準備する。以後は呼び直さない。
    found = RecordLookup(idea.memory_index, idea.conversation).evidence(
        "ep-long", cue, memory._recaller, limit=2
    )
    assert found[0].text == long_text


def test_lookup_trace_counts_once_and_uses_existing_embeddings(tmp_path):
    idea = _setup_memory(tmp_path, page=True)
    calls = []

    def embed(text):
        calls.append(text)
        return _embed(text)

    params = replace(load_recall_params(), threshold=100.0, intent_relief=0, noise=0)
    memory = Memory(idea, embed=embed, embed_model="test", params=params)
    cue = Cue("高野漁港って覚えてる？", (), NOW)
    remembered = memory.recall(cue)
    assert remembered == []
    before = len(calls)
    assert memory.lookup(cue, remembered, max_lines=6, neighbors=1)
    assert len(calls) == before
    assert len(memory.recall_log.times()["ep-past"]) == 1
    assert memory.recall_log.entries_on(NOW.date())[0]["intent"] == "lookup"

    memory.reload()
    memory._recaller.params = replace(params, threshold=-100, intent_relief=0)
    recalled = memory.recall(cue)
    assert any(m.page_id == "ep-past" for m in recalled)
    before = len(memory.recall_log.times()["ep-past"])
    memory.lookup(cue, recalled, max_lines=6, neighbors=1)
    assert len(memory.recall_log.times()["ep-past"]) == before


class Brain:
    def __init__(self, *, broken=False):
        self.calls = 0
        self.judges = 0
        self.packs = []
        self.enabled = True
        self.broken = broken

    def judge(self, prompt):
        self.judges += 1
        return {"search": False, "home": [999] if self.broken else []}

    def converse(self, pack, **_):
        self.calls += 1
        self.packs.append(pack)
        return {"reply": "返事"}


class RecordsOnly:
    def recall(self, cue, *, already=()):
        return []

    def lookup(self, cue, remembered, *, max_lines, neighbors):
        return ["【記録を調べた結果】\n2026-10-12 マスター: 秘密の行。"]

    def waking(self):
        return None

    def relation(self, person):
        return None


def _core(tmp_path, *, broken=False, thresholds=None):
    local, cloud = Brain(broken=broken), Brain()
    marks = HomeMarks(tmp_path)
    core = Core(
        persona_text="人格。", absolute_rules="守る。",
        thresholds=thresholds or ThresholdsConfig(), memory=RecordsOnly(),
        home_marks=marks, registry=[
            BrainEntry("gemma", "ollama", "local", "primary", "small"),
            BrainEntry("gemini", "gemini", "cloud", "conversation", "large"),
        ], brains={"gemma": local, "gemini": cloud}
    )
    core._decide_deep_thinking = lambda *_: False
    return core, marks, local, cloud


def test_unmarked_and_home_records_never_reach_gemini(tmp_path):
    core, marks, local, cloud = _core(tmp_path, broken=True)
    core.turn_routed("覚えてる？", now=NOW)
    assert local.calls == 1 and cloud.calls == 0
    assert local.judges == 1  # 追加の判定呼び出しはしない

    core, marks, local, cloud = _core(tmp_path / "home")
    marks.mark(["2026-10-12 マスター: 秘密の行。"], [1])
    core.turn_routed("覚えてる？", now=NOW)
    assert local.calls == 0 and cloud.calls == 1
    assert '秘密の行。' not in cloud.packs[0].render()
    assert local.judges == 1


def test_record_heading_does_not_hide_existing_home_mark(tmp_path):
    marks = HomeMarks(tmp_path)
    marks.mark(["秘密の行。"], [1])
    record = Record("2026-10-12#1", NOW, "master", "秘密の行。")
    assert marks.home(record.format())


def test_lookup_never_overfills_the_context_or_line_limit(tmp_path):
    thresholds = replace(
        ThresholdsConfig(), lookup_small_chars=160, lookup_total_small_chars=400,
        lookup_max_lines=2,
    )
    core, _, _, _ = _core(tmp_path, thresholds=thresholds)
    core._lookup_chunks = [
        "【記録を調べた結果】\n" + "\n".join("2026-10-12 マスター: " + "あ" * 50 for _ in range(5))
    ]
    pack = core._build_pack("覚えてる？", [], context_size="small")
    assert len(pack.render()) <= 400
    assert sum(len(x.splitlines()) - 1 for x in pack.lookup_records) <= 2

    core.persona_text = "非常に長い人格。" * 100
    assert core._build_pack("覚えてる？", [], context_size="small").lookup_records == ()


def test_lookup_context_budget_counts_render_separators(tmp_path):
    core, _, _, _ = _core(tmp_path)
    block = "【記録を調べた結果】\n2026-10-10 マスター:\n港へ行った。"
    base_length = len(core._build_pack("覚えてる？", [], context_size="small").render())
    core._lookup_chunks = [block]
    core.thresholds = replace(
        core.thresholds, lookup_small_chars=500,
        lookup_total_small_chars=base_length + len(block) + 1,
    )
    pack = core._build_pack("覚えてる？", [], context_size="small")
    assert len(pack.render()) <= core.thresholds.lookup_total_small_chars

    core.thresholds = replace(core.thresholds, lookup_total_small_chars=base_length + len(block) + 2)
    pack = core._build_pack("覚えてる？", [], context_size="small")
    assert pack.lookup_records == (block,)
    assert len(pack.render()) == core.thresholds.lookup_total_small_chars
