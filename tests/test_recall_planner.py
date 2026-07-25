"""RecallPlanner のテスト。Wave 3 A1。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.recall_planner import EMPTY_PLAN, plan_recall, resolve_facts_for_plan
from serina.core.memory.store import MemoryStore, RecallParams
from serina.core.runtime import Core
from serina.core.config import ThresholdsConfig
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.state.routing_rules import RoutingRules

NOW = datetime(2026, 7, 19, 12, 0, tzinfo=timezone.utc)


def test_rule_based_temporal_plan() -> None:
    plan = plan_recall("昨日の約束、覚えてる？", now=NOW)

    assert plan.queries
    assert any(q.type == "temporal" for q in plan.queries)
    assert plan.why.startswith("規則:")


def test_judge_failure_returns_empty_plan() -> None:
    def broken_judge(prompt: str) -> dict:
        raise RuntimeError("judge 失敗")

    plan = plan_recall("なんとなく話したい", now=NOW, judge=broken_judge)

    assert plan.queries == EMPTY_PLAN.queries


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])


def test_facts_bundled_separately_from_activation_recall() -> None:
    db_path = Path(tempfile.mkdtemp()) / "planner.db"
    store = MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0, activation_floor=0.0),
    )
    store.add_memory("無関係な山の話", type="fact", importance=0.1)
    store.facts.add_fact(
        subject="マスター",
        predicate="visited",
        object="東京",
        statement="昨日マスターは東京に行った",
        episode_ids=[1],
        status="active",
        valid_from="2026-07-18T00:00:00+00:00",
    )

    from serina.core.memory.recall_planner import RecallPlan, RecallQuery, RecallBudget

    plan = RecallPlan(
        queries=[
            RecallQuery(
                type="temporal",
                q="昨日",
                time_range=("2026-07-18T00:00:00+00:00", "2026-07-18T23:59:59+00:00"),
            ),
        ],
        budget=RecallBudget(max_memories=5, max_tokens=2000),
        why="テスト",
    )
    facts = resolve_facts_for_plan(plan, store.facts)
    memories = store.recall("山の話", top_k=5)

    assert any("東京" in f.statement for f in facts)
    assert not any("東京" in m.content for m in memories), "facts は活性化 recall に混ぜない"


class JudgeBrain:
    def __init__(self, script: dict | None = None) -> None:
        self.script = script or {}

    def judge(self, prompt: str) -> dict:
        return self.script

    def converse(self, pack, *, think: bool = False) -> dict:  # noqa: ANN001
        return {
            "reply": "了解",
            "fusen_list": [],
            "self_assessment": {"over_capacity": False, "reason": "test"},
        }


def test_runtime_planner_uses_rule_without_judge_crash() -> None:
    db_path = Path(tempfile.mkdtemp()) / "runtime_planner.db"
    store = MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )
    registry = [BrainEntry("primary", "ollama", "local", "primary", -1, -1, "small")]
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        memory_store=store,
        registry=registry,
        quota_ledger=QuotaLedger(),
        routing_rules=RoutingRules(),
        brains={"primary": JudgeBrain()},
    )

    result = core.turn_routed("昨日の話覚えてる？", now=NOW)

    assert result.report.reply == "了解"
