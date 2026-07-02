"""Core 本体（1ターン統括）"""

from __future__ import annotations

import logging
from typing import Any

from serina.core import context
from serina.core.config import CoreConfig
from serina.core.router import select
from serina.memory.store import MemoryStore
from serina.skills.base import Skill, SkillContext

logger = logging.getLogger(__name__)


class Core:
    def __init__(
        self,
        store: MemoryStore,
        persona: str,
        skills: list[Skill],
        router=select,
        config: CoreConfig | None = None,
    ) -> None:
        self.store = store
        self.persona = persona
        self.skills = skills
        self.router = router
        self.config = config or CoreConfig()

    def turn(self, session_id: str, user_input: str) -> dict[str, Any]:
        pinned_mems: list[dict[str, Any]] = []
        mems: list[dict[str, Any]] = []
        try:
            pinned_mems = self.store.get_all_pinned()
            mems = self.store.search(user_input, k=self.config.memory_k)
        except Exception:
            logger.exception("記憶検索に失敗しました。人格のみで続行します。")

        history = self.store.get_recent_history(session_id, self.config.history_n)
        history_msgs = context.to_messages(history)
        reference_mems = [m for m in mems if not m.get("pinned")]
        system = context.build_system(self.persona, pinned_mems, reference_mems, self.config)

        skill = self.router(user_input, self.skills)
        ctx = SkillContext(user_input, system, history_msgs)
        reply = skill.run(ctx)

        self.store.add_history(session_id, "user", user_input)
        self.store.add_history(session_id, "assistant", reply)

        return {
            "reply": reply,
            "skill": skill.name,
            "retrieved": mems,
            "system_len": len(system),
        }


def create_core(config: CoreConfig | None = None) -> Core:
    """CLI / テスト用の標準 Core 組み立て"""
    from serina.connectors.chat_llm import OllamaChatConnector
    from serina.connectors.embedder import OllamaEmbedder
    from serina.prompt.loader import load_persona
    from serina.skills.chat import ChatSkill

    cfg = config or CoreConfig()
    OllamaChatConnector.ensure_model_available(cfg.model, cfg.base_url)

    store = MemoryStore(OllamaEmbedder(base_url=cfg.base_url))
    persona = load_persona()
    connector = OllamaChatConnector(cfg.model, cfg.base_url)
    chat_skill = ChatSkill(connector, options={"temperature": cfg.temperature})
    return Core(store, persona, [chat_skill], config=cfg)
