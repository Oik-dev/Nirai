"""セッション状態: 会話原文＋転がし要約。設計書 §2.6, §1.4"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    speaker: str  # "master" or "serina"
    text: str
    location: str | None = None  # "cloud" | "local" | None(未追跡)。§3.3第3経路の前提
    ts: str | None = None  # 発言時刻のUTC ISO文字列。省略時は日付帰属を発話時刻に紐付けない（後方互換）


class SessionState:
    def __init__(self) -> None:
        self.turns: list[Turn] = []
        self.rolling_summary: str = ""
        # 直近帯の細かめ要約（§1.5 ⑦）。LLM更新本体は別タスク。
        self.fine_summary: str = ""
        # 先頭から何ターン分をrolling_summaryへ折り込んだか（アイドル時の要約消化用）
        self.summarized_turn_count: int = 0

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
