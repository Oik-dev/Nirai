"""実運用向けCore組み立て。設計書 §5.5-4, §1.2, §3.1。

本番では persona / thresholds / registry / 実アダプタ / 実DB を束ねる。
テストでは `Core(...)` を手組みしてよい。
"""

from __future__ import annotations

from pathlib import Path

from serina.brains.aurora.adapter import AuroraAdapter
from serina.brains.gemini.adapter import GeminiAdapter
from serina.core.chores.chore_box import DEFAULT_CHORE_BOX_PATH, ChoreBox
from serina.core.config import load_thresholds
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore, RecallParams
from serina.core.routing.quota_ledger import DEFAULT_PERSIST_PATH as DEFAULT_QUOTA_LEDGER_PATH
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import load_brain_registry
from serina.core.runtime import Core
from serina.core.state.routing_rules import DEFAULT_PERSIST_PATH as DEFAULT_ROUTING_RULES_PATH
from serina.core.state.routing_rules import RoutingRules

ROOT = Path(__file__).resolve().parent.parent
PERSONA_PATH = ROOT / "prompt" / "persona.md"
BOUNDARY_PATH = ROOT / "prompt" / "boundary.md"
DEFAULT_MEMORY_DB_PATH = ROOT / "data" / "serina_memory.db"

# config/brains.toml の name -> 実際のGemini APIモデル名。
# brains.tomlはdaily_quota/per_minute_quotaでモデルの「枠」を表現するだけで、
# 実モデル名までは持たせていない（§3.1「登録簿は表であってコードではない」の対象外の情報）。
# MILESTONE.md「環境メモ」2026-07-10時点の無料枠に準拠。Geminiのモデル世代が変わったら
# ここだけ直せばよい。
GEMINI_MODEL_BY_BRAIN_NAME = {
    "gemini_flash_lite": "gemini-3.1-flash-lite",
    "gemini_flash": "gemini-3.5-flash",
}


def _build_brain(entry, gemini_api_key: str | None, thresholds):  # noqa: ANN001
    if entry.adapter == "aurora":
        return AuroraAdapter(
            max_extraction_retries=thresholds.aurora_extraction_max_retries,
            request_timeout_seconds=thresholds.aurora_request_timeout_seconds,
        )
    if entry.adapter == "gemini":
        if not gemini_api_key:
            # §3.5: 弾切れ・通信エラーと同じ扱いで「呼べない」状態にする。
            # decide_brain/_obtain_valid_reportが例外を握りつぶし代打に回すため、
            # ここで無理にNoneを返さずCloudRejectionErrorではなく素朴な例外を投げるBrainにする。
            return _UnavailableBrain(entry.name)
        model = GEMINI_MODEL_BY_BRAIN_NAME.get(entry.name)
        kwargs = {
            "api_key": gemini_api_key,
            "request_timeout_seconds": thresholds.gemini_request_timeout_seconds,
        }
        if model:
            return GeminiAdapter(model=model, **kwargs)
        return GeminiAdapter(**kwargs)
    raise ValueError(f"未知のadapter種別: {entry.adapter}（config/brains.tomlを確認）")


class _UnavailableBrain:
    """GEMINI_API_KEY未設定時のプレースホルダ。呼ばれたら即例外にして代打(fallback)へ回す。"""

    def __init__(self, name: str) -> None:
        self._name = name

    def converse(self, pack):  # noqa: ANN001
        raise RuntimeError(f"{self._name}: GEMINI_API_KEY未設定のため呼び出せない")


def create_core(
    *,
    gemini_api_key: str | None = None,
    memory_db_path: Path | str | None = None,
    chore_box_path: Path | str | None = None,
    routing_rules_path: Path | str | None = None,
    quota_ledger_path: Path | str | None = None,
) -> Core:
    """本番用の`core.runtime.Core`を組み立てる。

    gemini_api_keyが無い場合、cloud系Brain(gemini_flash_lite/gemini_flash)は
    呼び出し不能なプレースホルダになる（§3.5と同じ扱いで、常にAurora(fallback)へ落ちる。
    ローカルのみでも会話自体は成立する）。
    """
    persona_text = PERSONA_PATH.read_text(encoding="utf-8")
    absolute_rules = BOUNDARY_PATH.read_text(encoding="utf-8")
    thresholds = load_thresholds()
    registry = load_brain_registry()

    brains = {
        entry.name: _build_brain(entry, gemini_api_key, thresholds) for entry in registry
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

    return Core(
        persona_text=persona_text,
        absolute_rules=absolute_rules,
        thresholds=thresholds,
        memory_store=memory_store,
        registry=registry,
        quota_ledger=QuotaLedger(persist_path=quota_ledger_path or DEFAULT_QUOTA_LEDGER_PATH),
        routing_rules=RoutingRules(persist_path=routing_rules_path or DEFAULT_ROUTING_RULES_PATH),
        brains=brains,
        chore_box=chore_box,
    )
