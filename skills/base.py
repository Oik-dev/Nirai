"""Skill インターフェース（契約）"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SkillContext:
    user_input: str
    system_prompt: str
    history: list[dict[str, str]]


@runtime_checkable
class Skill(Protocol):
    name: str

    def can_handle(self, user_input: str) -> bool:
        ...

    def run(self, ctx: SkillContext) -> str:
        ...
