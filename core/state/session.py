"""セッション状態: 会話原文＋転がし要約。設計書 §2.6, §1.4"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    speaker: str  # "master" or "serina"
    text: str
    location: str | None = None  # "cloud" | "local" | None(未追跡)。§3.3第3経路の前提


class SessionState:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.rolling_summary: str = ""
        # 先頭から何ターン分をrolling_summaryへ折り込んだか（アイドル時の要約消化用）
        self.summarized_turn_count: int = 0

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
