"""実運用向けCore組み立て。設計書 §5.5-4, §1.2, §3.1。

本番では persona / thresholds / registry / 実アダプタ / 住人の長期記憶 を束ねる。
テストでは `Core(...)` を手組みしてよい。
"""

from __future__ import annotations

from pathlib import Path

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.config import load_thresholds
from mind.core.env import DEFAULT_ENV_PATH, load_env
from mind.core.feeling.feelings import Feelings
from mind.core.idea import IDEA
from mind.core.lifelog import FeelingLog
from mind.core.memory.embedder import DEFAULT_MODEL as EMBED_MODEL
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.memory import Memory
from mind.core.persona_assets import load_persona_assets
from mind.core.routing.quota_ledger import DEFAULT_PERSIST_PATH as DEFAULT_QUOTA_LEDGER_PATH
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import load_brain_registry
from mind.core.runtime import Core
from mind.core.state.routing_rules import DEFAULT_PERSIST_PATH as DEFAULT_ROUTING_RULES_PATH
from mind.core.state.routing_rules import RoutingRules
from mind.skills.gemini_advisor.skill import load_gemini_advisor
from mind.skills.tavily_search.skill import load_tavily_search

GEMINI_ENV_API_KEY = "GEMINI_API_KEY"
TAVILY_ENV_API_KEY = "TAVILY_API_KEY"


def _build_brain(entry, thresholds):  # noqa: ANN001
    if entry.adapter == "ollama":
        return OllamaAdapter(
            request_timeout_seconds=thresholds.ollama_request_timeout_seconds,
            num_ctx=thresholds.ollama_num_ctx,
            use_mmap=thresholds.ollama_use_mmap,
        )
    raise ValueError(f"未知のadapter種別: {entry.adapter}（config/brains.tomlを確認）")


def create_core(
    *,
    routing_rules_path: Path | str | None = None,
    quota_ledger_path: Path | str | None = None,
    gemini_env_path: Path | str | None = None,
    tavily_env_path: Path | str | None = None,
) -> Core:
    """本番用の`core.runtime.Core`を組み立てる。

    会話 Brain は単一構成（ローカル Ollama）。Gemini（無人格アドバイザー §5.6）と Tavily（自律検索）は
    `.env` のキーを注入する Skill としてのみ配線する（会話Brainには登録しない）。
    長期記憶は、このプロセスの住人のイデア（NIRAI_IDEA）の memory/ と索引を読む。気持ちは、同じイデアの気持ちの記録。
    """
    persona_assets = load_persona_assets()
    thresholds = load_thresholds()
    registry = load_brain_registry()

    brains = {
        entry.name: _build_brain(entry, thresholds) for entry in registry
    }

    embedder = OllamaEmbedder(request_timeout_seconds=thresholds.embedder_request_timeout_seconds)
    memory = Memory(IDEA, embed=embedder.embed, embed_model=EMBED_MODEL)
    primary = next(brains[entry.name] for entry in registry if entry.role == "primary")

    def warm() -> None:
        # 本人の脳を先に。大きな脳の読み込みは、載っているほかのモデルを追い出す（Ollama の記録 2026-10-06）ので、
        # あとから埋め込みを載せれば両方が載ったままになる。
        primary.warm()
        embedder.warm()

    # APIキーの取得はCoreの責務。Skillへは値を渡し切る（設計書 §1.3 の下り一方向）
    gemini_env = load_env(Path(gemini_env_path) if gemini_env_path else DEFAULT_ENV_PATH)
    gemini_advisor = load_gemini_advisor(
        api_key=gemini_env.get(GEMINI_ENV_API_KEY) or None,
    )
    tavily_env = load_env(Path(tavily_env_path) if tavily_env_path else DEFAULT_ENV_PATH)
    tavily_search = load_tavily_search(
        api_key=tavily_env.get(TAVILY_ENV_API_KEY) or None,
    )

    return Core(
        persona_text=persona_assets.persona_text,
        absolute_rules=persona_assets.absolute_rules,
        thresholds=thresholds,
        memory=memory,
        feelings=Feelings(FeelingLog(IDEA.feeling), thresholds.feeling),
        registry=registry,
        quota_ledger=QuotaLedger(persist_path=quota_ledger_path or DEFAULT_QUOTA_LEDGER_PATH),
        routing_rules=RoutingRules(persist_path=routing_rules_path or DEFAULT_ROUTING_RULES_PATH),
        brains=brains,
        gemini_advisor=gemini_advisor,
        tavily_search=tavily_search,
        warm=warm,
    )
