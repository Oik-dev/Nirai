"""Skill 選択（v1 は対話 Skill を返すだけ）"""

from __future__ import annotations

from serina.skills.base import Skill


def select(user_input: str, skills: list[Skill]) -> Skill:
    for skill in skills:
        if skill.can_handle(user_input):
            return skill
    raise RuntimeError(f"入力を処理できる Skill がありません: {user_input[:50]}")
