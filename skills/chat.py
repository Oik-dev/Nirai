"""直接応答 Skill（Connector 経由で Aurora 呼出）"""

from __future__ import annotations

from typing import Any

from serina.connectors.chat_llm import ChatConnector
from serina.skills.base import SkillContext


class ChatSkill:
    name = "chat"

    def __init__(
        self,
        connector: ChatConnector,
        options: dict[str, Any] | None = None,
    ) -> None:
        self.connector = connector
        self.options = options or {}

    def can_handle(self, user_input: str) -> bool:
        return True

    def run(self, ctx: SkillContext) -> str:
        messages = ctx.history + [{"role": "user", "content": ctx.user_input}]
        return self.connector.chat(ctx.system_prompt, messages, self.options)
