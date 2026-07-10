"""Core本体。設計書v2 第1章〜第2章, §4(記憶接続)。

会話 → 想起(長期記憶) → 文脈パック組み立て → Brain呼び出し
 → 関所①②③ → 状態更新 → 記憶候補の審査ライン(関所④) → セッションへ記録。
"""

from __future__ import annotations

from typing import Protocol

from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.intake.gate import IntakeResult, process_report
from serina.core_v2.intake.memory_review import review_candidate
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.state.emotion import EmotionState
from serina.core_v2.state.relationship import RelationshipState
from serina.core_v2.state.session import SessionState, Turn

RECALL_TOP_K = 5


class Brain(Protocol):
    def converse(self, pack) -> dict: ...  # noqa: ANN001


class Core:
    def __init__(
        self,
        persona_text: str,
        absolute_rules: str,
        thresholds: ThresholdsConfig,
        memory_store: MemoryStore | None = None,
    ) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.thresholds = thresholds
        self.memory_store = memory_store
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()
        self.session_candidate_count = 0

    def turn(self, master_utterance: str, brain: Brain) -> IntakeResult:
        recalled_memories = (
            self.memory_store.recall(master_utterance, top_k=RECALL_TOP_K) if self.memory_store else None
        )
        pack = build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            session=self.session,
            master_utterance=master_utterance,
            recalled_memories=recalled_memories,
        )
        raw_report = brain.converse(pack)

        result = process_report(
            raw_report,
            emotion=self.emotion,
            relationship=self.relationship,
            thresholds=self.thresholds,
        )

        self.session.add_turn(Turn(speaker="master", text=master_utterance))
        self.session.add_turn(Turn(speaker="serina", text=result.report.reply))

        if self.memory_store:
            for fusen in result.accepted_fusen:
                if fusen.kind != "記憶候補":
                    continue
                review = review_candidate(
                    fusen,
                    session=self.session,
                    store=self.memory_store,
                    thresholds=self.thresholds,
                    session_candidate_count=self.session_candidate_count,
                )
                if review.accepted:
                    self.session_candidate_count += 1

        return result
