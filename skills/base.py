"""Skill インターフェース（契約）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable


@dataclass
class SkillContext:
    user_input: str
    system_prompt: str
    history: list[dict[str, str]]
    on_token: Callable[[str], None] | None = None  # ストリーミング表示用（未対応Skillは無視してよい）
    # ① Decision / ③ Evidence を Voice へ読み取り専用で渡す（frozen なので書き換え不可＝逆流の壁）
    decision: Any = None
    evidence: Any = None


@runtime_checkable
class Skill(Protocol):
    name: str

    def can_handle(self, user_input: str) -> bool:
        ...

    def run(self, ctx: SkillContext) -> str:
        ...
