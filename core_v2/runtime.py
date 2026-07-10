"""Core本体。設計書v2 第1章〜第2章。Phase1範囲: 記憶接続なしの最小会話ループ。

会話 → 文脈パック組み立て → Brain呼び出し → 関所①②③ → 状態更新 → セッションへ記録。
"""

from __future__ import annotations

from typing import Protocol

from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.context.pack import build_context_pack
from serina.core_v2.intake.gate import IntakeResult, process_report
from serina.core_v2.state.emotion import EmotionState
from serina.core_v2.state.relationship import RelationshipState
from serina.core_v2.state.session import SessionState, Turn


class Brain(Protocol):
    def converse(self, pack) -> dict: ...  # noqa: ANN001


class Core:
    def __init__(self, persona_text: str, absolute_rules: str, thresholds: ThresholdsConfig) -> None:
        self.persona_text = persona_text
        self.absolute_rules = absolute_rules
        self.thresholds = thresholds
        self.emotion = EmotionState()
        self.relationship = RelationshipState()
        self.session = SessionState()

    def turn(self, master_utterance: str, brain: Brain) -> IntakeResult:
        pack = build_context_pack(
            persona_text=self.persona_text,
            absolute_rules=self.absolute_rules,
            session=self.session,
            master_utterance=master_utterance,
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

        return result
