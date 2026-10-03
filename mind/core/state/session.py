"""セッション状態: 手元の会話の流れ（原文＋転がし要約）と、この会話の中で思い出したもの。設計書 §2.6, §1.4

出来事は眠ったあとから思い出せる（core/memory/recall.py）。それまでの話は、記憶ではなくここにある。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Turn:
    speaker: str  # "master" or "serina"
    text: str
    location: str | None = None  # "cloud" | "local" | None(未追跡)。§3.3第3経路の前提
    ts: str | None = None  # 発言時刻のUTC ISO文字列（帳簿から読み直した発言にもある）


class SessionState:
    def __init__(self, turns: Iterable[Turn] = ()) -> None:
        self.turns: list[Turn] = list(turns)
        self.rolling_summary: str = ""
        # 直近帯の細かめ要約（§1.5 ⑦）。
        self.fine_summary: str = ""
        # 先頭から何ターン分をrolling_summaryへ折り込んだか
        self.summarized_turn_count: int = 0
        # この会話の中で、もう浮かんだ記憶のページ（自然には浮かび直さない。core/memory/recall.py）
        self.recalled: set[str] = set()

    def add_turn(self, turn: Turn) -> None:
        self.turns.append(turn)
