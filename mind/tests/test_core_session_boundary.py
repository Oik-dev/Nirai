"""Core のセッション（手元の会話の流れ）と、会話のたびに思い出すこと。設計書 §2.6, §4。

守るもの：
- 思い出したことが、そのターンの文脈パックに載る。何も浮かばなければ段ごと載らない（黙る）。
- 思い出す手がかりは、今の発言と直前の会話（同じセッションの最後の4発言）。
- 同じ会話の中ですでに浮かんだページは、記憶に「もう浮かんだ」と伝える（自然には浮かび直さない）。
- セッションの境界で、手元の流れと「もう浮かんだ」は新しくなる。keep で渡した発言（まだ眠っていない今日の発言）は残る。
LLM不要（StubBrain と、手がかりを控える記憶の替え玉）。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig
from mind.core.memory.recall import Remembered
from mind.core.runtime import Core
from mind.core.state.session import Turn

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


class StubBrain:
    def __init__(self) -> None:
        self.packs = []

    def converse(self, pack, **_) -> dict:  # noqa: ANN001
        self.packs.append(pack.render())
        return {"reply": "うん"}


class RecordingMemory:
    """思い出す手がかりを控え、決めておいたページを浮かべる記憶の替え玉。"""

    def __init__(self, floats: list[list[str]]) -> None:
        self.floats = floats
        self.cues = []
        self.already = []

    def recall(self, cue, *, already=()):  # noqa: ANN001, ANN201
        self.cues.append(cue)
        self.already.append(set(already))
        pages = self.floats.pop(0) if self.floats else []
        return [Remembered(pid, f"（思い出した）{pid}", None, "", 2.0, True, False) for pid in pages]


def _core(memory=None) -> Core:  # noqa: ANN001
    thresholds = ThresholdsConfig()
    return Core(persona_text="人格", absolute_rules="ルール", thresholds=thresholds, memory=memory)


def test_remembered_pages_enter_the_pack() -> None:
    memory = RecordingMemory([["ep-2025-03-07-01"]])
    brain = StubBrain()
    _core(memory).turn("海の話、覚えてる？", brain, now=NOW)
    assert "【思い出したこと】" in brain.packs[0]
    assert "（思い出した）ep-2025-03-07-01" in brain.packs[0]


def test_nothing_came_to_mind_means_no_memory_section() -> None:
    brain = StubBrain()
    _core(RecordingMemory([[]])).turn("まだ仕事中だわ", brain, now=NOW)
    assert "【思い出したこと】" not in brain.packs[0]


def test_cue_is_the_utterance_and_the_last_four_turns() -> None:
    memory = RecordingMemory([])
    core = _core(memory)
    brain = StubBrain()
    for text in ("一", "二", "三"):
        core.turn(text, brain, now=NOW)
    core.turn("四", brain, now=NOW)
    cue = memory.cues[-1]
    assert cue.utterance == "四"
    assert cue.now == NOW
    assert [text for _, text in cue.recent] == ["二", "うん", "三", "うん"]


def test_pages_already_floated_in_this_conversation_are_passed_on() -> None:
    memory = RecordingMemory([["ep-a"], ["ep-b"], []])
    core = _core(memory)
    brain = StubBrain()
    core.turn("一", brain, now=NOW)
    core.turn("二", brain, now=NOW)
    core.turn("三", brain, now=NOW)
    assert memory.already == [set(), {"ep-a"}, {"ep-a", "ep-b"}]


def test_end_session_starts_a_new_flow() -> None:
    memory = RecordingMemory([["ep-a"]])
    core = _core(memory)
    core.turn("やあ", StubBrain(), now=NOW)
    core.session.fine_summary = "要約"
    assert len(core.session.turns) == 2

    core.end_session()

    assert core.session.turns == []
    assert core.session.fine_summary == ""
    assert core.session.recalled == set()


def test_end_session_keeps_given_turns() -> None:
    core = _core()
    today = [Turn(speaker="master", text="おはよう", ts=NOW.isoformat())]
    core.end_session(keep=today)
    assert core.session.turns == today


def test_recall_failure_does_not_stop_the_turn() -> None:
    class Broken:
        def recall(self, cue, *, already=()):  # noqa: ANN001, ANN201, ARG002
            raise RuntimeError("埋め込みが瞬断した")

    brain = StubBrain()
    result = _core(Broken()).turn("聞こえる？", brain, now=NOW)
    assert result.report.reply == "うん"
    assert "【思い出したこと】" not in brain.packs[0]
