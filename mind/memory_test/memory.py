"""テストを受ける記憶の口。記憶の仕組みを替えても、この形で答える（替えた仕組みを同じ問題で比べるため）。

記憶は、手がかり（今の発言・直前の会話・今の時刻）を受け取り、住人の手元に渡すものを返す。
返さないことが正しいときもある（関係ない話では黙る）。浮かべるか黙るかは記憶が決め、テストは指図しない。
now より後の記録から思い出してはいけない（過去の時点を再現して測るため）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from mind.memory_test.cases import Turn


@dataclass(frozen=True)
class Cue:
    utterance: str
    recent: tuple[Turn, ...]  # 古い順
    now: datetime


@dataclass(frozen=True)
class Recalled:
    text: str  # 住人の手元に渡す文
    when: date | None = None  # 思い出した出来事の日（分からなければ None）
    evidence: str = ""  # この記憶が拠っている記録の原文。目印はここと text から探す（ページの題だけを渡すときなど）


class Memory(Protocol):
    name: str

    def recall(self, cue: Cue) -> list[Recalled]: ...
