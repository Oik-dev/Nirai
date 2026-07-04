"""Core 本体（1ターン統括）"""

from __future__ import annotations

import logging
import random
from datetime import datetime
from typing import Any, Callable

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

    def turn(
        self,
        session_id: str,
        user_input: str,
        on_token: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
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

        # 3c: 感情ステート（narrative_mood＋照れ隠し判定）／4c: 成長層（self_image）
        emotion: dict[str, Any] | None = None
        self_image: str | None = None
        try:
            intimacy_raw = self.store.get_profile("emotion.intimacy")
            emotion = {
                "narrative_mood": self.store.get_profile("emotion.narrative_mood"),
                "shy": (
                    intimacy_raw is not None
                    and float(intimacy_raw) >= self.config.shy_threshold
                ),
            }
            self_image = self.store.get_profile("self_image") or None
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
        # ※一度きりの演出（used化・クリア）は応答成功後に確定する（LLM失敗での空撃ち防止）
        open_threads: list[dict[str, Any]] = []
        growth_note: str | None = None
        growth_note_mem: dict[str, Any] | None = None
        idle_thought: str | None = None
        if len(history) < self.config.open_thread_inject_history_max:
            try:
                open_threads = self.store.list_open_threads(self.config.open_thread_max_inject)
            except Exception:
                logger.exception("未解決スレッドの取得に失敗しました。注入なしで続行します。")
            try:
                # 差分想起: 確率 p_growth で最新の未使用 growth_note を1件（選定のみ）
                if self.rng.random() < self.config.p_growth:
                    for note in self.store.list_memories_by_type("growth_note", limit=20):
                        meta = note.get("metadata") or {}
                        if isinstance(meta, dict) and not meta.get("used"):
                            growth_note = note["content"]
                            growth_note_mem = note
                            break
                idle_thought = self.store.get_profile("idle_thought") or None
            except Exception:
                logger.exception("差分想起/独り言の取得に失敗しました。注入なしで続行します。")

        # スライス2: 現在時刻の常時注入（ローカル時刻・曜日つき）
        now_text: str | None = None
        if self.config.inject_time:
            local = datetime.now().astimezone()
            weekday = "月火水木金土日"[local.weekday()]
            now_text = f"現在時刻: {local.strftime('%Y-%m-%d')}（{weekday}） {local.strftime('%H:%M')}"

        system = context.build_system(
            self.persona, static_mems, reference_mems, self.config,
            open_threads, emotion, growth_note, idle_thought, self_image, now_text,
        )

        skill = self.router(user_input, self.skills)
        ctx = SkillContext(user_input, system, history_msgs, on_token=on_token)
        reply = skill.run(ctx)

        self.store.add_history(session_id, "user", user_input)
        self.store.add_history(session_id, "assistant", reply)

        # 4b: 応答が成功してから一度きり演出を消費確定（使用後は再注入しない／再放送は幻滅イベント）
        try:
            if growth_note_mem is not None:
                meta = growth_note_mem.get("metadata") or {}
                if isinstance(meta, dict):
                    meta["used"] = True
                    self.store.update(growth_note_mem["id"], metadata=meta)
            if idle_thought:
                self.store.set_profile("idle_thought", "")
        except Exception:
            logger.exception("差分想起/独り言の消費確定に失敗しました（次回再注入され得ます）")

        # 4c: 各ターンの想起IDを記録（コールバック率の原簿）
        if self.config.metrics_enabled:
            try:
                used_ids = [m["id"] for m in static_mems] + [m["id"] for m in reference_mems]
                self.store.add_turn_retrieval(session_id, used_ids)
            except Exception:
                logger.exception("想起IDの記録に失敗しました（対話は継続）")

        return {
            "reply": reply,
            "skill": skill.name,
            "retrieved": mems,
            "system_len": len(system),
        }


def create_core(config: CoreConfig | None = None) -> Core:
    """CLI / テスト用の標準 Core 組み立て"""
    import os

    from serina.connectors.chat_llm import OllamaChatConnector
    from serina.connectors.embedder import OllamaEmbedder
    from serina.connectors.search import SearXNGConnector
    from serina.prompt.loader import load_persona
    from serina.skills.distill_intent import DistillIntentSkill
    from serina.skills.web_search import WebSearchSkill

    cfg = config or CoreConfig()
    OllamaChatConnector.ensure_model_available(cfg.model, cfg.base_url)

    store = MemoryStore(OllamaEmbedder(base_url=cfg.base_url))
    persona = load_persona()
    connector = OllamaChatConnector(cfg.model, cfg.base_url)
    search_connector = SearXNGConnector(
        base_url=os.environ.get("SERINA_SEARXNG_URL", cfg.searxng_url),
        timeout=cfg.search_timeout,
        top_n=cfg.search_top_n,
        char_cap=cfg.search_snippet_char_cap,
    )
    web_skill = WebSearchSkill(
        connector,
        search_connector,
        options={
            "temperature": cfg.temperature,
            "num_ctx": cfg.num_ctx,
            "repeat_penalty": cfg.repeat_penalty,
        },
    )
    # ルーティング: 優先順マッチ。WebSearchSkill が can_handle=True のキャッチオール兼会話本体
    return Core(store, persona, [DistillIntentSkill(), web_skill], config=cfg)
