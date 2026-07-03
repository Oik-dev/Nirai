"""Core 本体（1ターン統括）"""

from __future__ import annotations

import logging
import random
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
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.persona = persona
        self.skills = skills
        self.router = router
        self.config = config or CoreConfig()
        self.rng = rng or random.Random()

    def turn(self, session_id: str, user_input: str) -> dict[str, Any]:
        mems: list[dict[str, Any]] = []
        try:
            mems = self.store.search(user_input, k=self.config.memory_k)
        except Exception:
            logger.exception("記憶検索に失敗しました。人格のみで続行します。")

        # 3c: 静的マウント（二経路想起のトリガー側＋ホット層昇格）。減衰スコアを無視して強制注入
        static_mems: list[dict[str, Any]] = []
        try:
            seen: set[int] = set()
            triggered = self.store.list_triggered(user_input)
            hot = self.store.list_hot_memories(
                self.config.hot_min_access,
                self.config.hot_recent_days,
                self.config.hot_max_items,
            )
            for m in triggered + hot:  # トリガー優先、余った予算にホット層
                if m["id"] not in seen:
                    seen.add(m["id"])
                    static_mems.append(m)
        except Exception:
            logger.exception("静的マウントの取得に失敗しました。注入なしで続行します。")

        # 3c: 感情ステート（narrative_mood＋照れ隠し判定）
        emotion: dict[str, Any] | None = None
        try:
            intimacy_raw = self.store.get_profile("emotion.intimacy")
            emotion = {
                "narrative_mood": self.store.get_profile("emotion.narrative_mood"),
                "shy": (
                    intimacy_raw is not None
                    and float(intimacy_raw) >= self.config.shy_threshold
                ),
            }
        except Exception:
            logger.exception("感情ステートの取得に失敗しました。注入なしで続行します。")

        history = self.store.get_recent_history(session_id, self.config.history_n)
        history_msgs = context.to_messages(history)
        static_ids = {m["id"] for m in static_mems}
        reference_mems = [
            m for m in mems
            if m["id"] not in static_ids
            and m.get("relevance", 1.0) >= self.config.memory_min_relevance
        ]

        # セッション冒頭のみ: 未解決スレッド（3b）＋差分想起・独り言（4b）
        open_threads: list[dict[str, Any]] = []
        growth_note: str | None = None
        idle_thought: str | None = None
        if len(history) < self.config.open_thread_inject_history_max:
            try:
                open_threads = self.store.list_open_threads(self.config.open_thread_max_inject)
            except Exception:
                logger.exception("未解決スレッドの取得に失敗しました。注入なしで続行します。")
            try:
                # 差分想起: 確率 p_growth で最新の未使用 growth_note を1件。使用後は再注入しない
                if self.rng.random() < self.config.p_growth:
                    for note in self.store.list_memories_by_type("growth_note", limit=20):
                        meta = note.get("metadata") or {}
                        if isinstance(meta, dict) and not meta.get("used"):
                            growth_note = note["content"]
                            meta["used"] = True
                            self.store.update(note["id"], metadata=meta)
                            break
                # 独り言: 使用したら必ずクリア（同じ独り言の再放送は幻滅イベント）
                idle_thought = self.store.get_profile("idle_thought") or None
                if idle_thought:
                    self.store.set_profile("idle_thought", "")
            except Exception:
                logger.exception("差分想起/独り言の取得に失敗しました。注入なしで続行します。")

        system = context.build_system(
            self.persona, static_mems, reference_mems, self.config,
            open_threads, emotion, growth_note, idle_thought,
        )

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
    chat_skill = ChatSkill(
        connector,
        options={
            "temperature": cfg.temperature,
            "num_ctx": cfg.num_ctx,
            "repeat_penalty": cfg.repeat_penalty,
        },
    )
    return Core(store, persona, [chat_skill], config=cfg)
