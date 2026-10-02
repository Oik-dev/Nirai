"""実運用向けCore組み立て。設計書 §5.5-4, §1.2, §3.1。

本番では persona / thresholds / registry / 実アダプタ / 実DB を束ねる。
テストでは `Core(...)` を手組みしてよい。
"""

from __future__ import annotations

from pathlib import Path

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.chores.chore_box import DEFAULT_CHORE_BOX_PATH, ChoreBox
from mind.core.config import load_thresholds
from mind.core.env import DEFAULT_ENV_PATH, load_env
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.protection import ChangeLog
from mind.core.memory.store import MemoryStore, RecallParams
from mind.core.chores.summaries import load_summary_blocks, render_summary_blocks_for_pack
from mind.core.persona_assets import load_persona_assets
from mind.core.routing.quota_ledger import DEFAULT_PERSIST_PATH as DEFAULT_QUOTA_LEDGER_PATH
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import load_brain_registry
from mind.core.runtime import Core
from mind.core.soul import DATA_DIR
from mind.core.state.routing_rules import DEFAULT_PERSIST_PATH as DEFAULT_ROUTING_RULES_PATH
from mind.core.state.routing_rules import RoutingRules
from mind.skills.gemini_advisor.skill import load_gemini_advisor
from mind.skills.tavily_search.skill import load_tavily_search

DEFAULT_MEMORY_DB_PATH = DATA_DIR / "serina_memory.db"
GEMINI_ENV_API_KEY = "GEMINI_API_KEY"
TAVILY_ENV_API_KEY = "TAVILY_API_KEY"


def _build_brain(entry, thresholds):  # noqa: ANN001
    if entry.adapter == "ollama":
        return OllamaAdapter(
            request_timeout_seconds=thresholds.ollama_request_timeout_seconds,
            num_ctx=thresholds.ollama_num_ctx,
        )
    raise ValueError(f"未知のadapter種別: {entry.adapter}（config/brains.tomlを確認）")


def create_core(
    *,
    memory_db_path: Path | str | None = None,
    chore_box_path: Path | str | None = None,
    routing_rules_path: Path | str | None = None,
    quota_ledger_path: Path | str | None = None,
    gemini_env_path: Path | str | None = None,
    tavily_env_path: Path | str | None = None,
    change_log: ChangeLog | None = None,
) -> Core:
    """本番用の`core.runtime.Core`を組み立てる。

    2026-07-18: Brain構成刷新（合意台帳 §9）により会話 Brain は単一構成（ローカル Ollama）。
    Gemini は会話 Brain ではなく、`.env` の `GEMINI_API_KEY` を注入する
    無人格アドバイザー Skill（§5.6）としてのみ配線する。
    2026-07-31: Tavily（自律検索・裏方の道具）も同様に`.env`の`TAVILY_API_KEY`を注入する
    Skillとしてのみ配線する（会話Brainには登録しない）。

    `change_log` を渡すと Core に配線され、予定の即時書き込みレポート等が CLI 等でも残る。
    """
    persona_assets = load_persona_assets()
    persona_text = persona_assets.persona_text
    absolute_rules = persona_assets.absolute_rules
    summary_blocks = load_summary_blocks()
    prefs_summary, relation_summary = render_summary_blocks_for_pack(summary_blocks)
    thresholds = load_thresholds()
    registry = load_brain_registry()

    brains = {
        entry.name: _build_brain(entry, thresholds) for entry in registry
    }

    memory_store = MemoryStore(
        str(memory_db_path or DEFAULT_MEMORY_DB_PATH),
        embedder=OllamaEmbedder(
            request_timeout_seconds=thresholds.embedder_request_timeout_seconds,
        ),
        recall_params=RecallParams(
            weight_relevance=thresholds.recall_weight_relevance,
            weight_importance=thresholds.recall_weight_importance,
            weight_recency=thresholds.recall_weight_recency,
            grade_bonus_s=thresholds.recall_grade_bonus_s,
            grade_bonus_a=thresholds.recall_grade_bonus_a,
            spread_decay=thresholds.recall_spread_decay,
            spread_seeds=thresholds.recall_spread_seeds,
            noise_sigma=thresholds.recall_noise_sigma,
            activation_floor=thresholds.recall_activation_floor,
        ),
    )
    chore_box = ChoreBox(Path(chore_box_path) if chore_box_path else DEFAULT_CHORE_BOX_PATH)
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
        persona_text=persona_text,
        absolute_rules=absolute_rules,
        prefs_summary=prefs_summary,
        relation_summary=relation_summary,
        thresholds=thresholds,
        memory_store=memory_store,
        registry=registry,
        quota_ledger=QuotaLedger(persist_path=quota_ledger_path or DEFAULT_QUOTA_LEDGER_PATH),
        routing_rules=RoutingRules(persist_path=routing_rules_path or DEFAULT_ROUTING_RULES_PATH),
        brains=brains,
        chore_box=chore_box,
        gemini_advisor=gemini_advisor,
        tavily_search=tavily_search,
        change_log=change_log,
    )
