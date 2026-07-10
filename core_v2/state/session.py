"""セッション状態: 会話原文＋転がし要約。設計書v2 §2.6, §1.4"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    speaker: str  # "master" or "serina"
    text: str


class SessionState:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.rolling_summary: str = ""

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
