"""記憶ツール（Core 関所）のテスト。Wave 3 A6。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.contract.schema import parse_memory_tool_calls_lenient
from serina.core.config import ThresholdsConfig
from serina.core.intake.gate import process_report
from serina.core.intake.memory_tools import execute_memory_tool_calls, parse_memory_tool_calls
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore, RecallParams
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState


def _fake_embedder() -> OllamaEmbedder:
    vectors = {"海": [0.0, 1.0, 0.0, 0.0]}

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [1.0, 0.0, 0.0, 0.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "tools.db"
    return MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def test_memory_search_and_get_via_gate() -> None:
    store = _fresh_store()
    mid = store.add_memory("海に行った思い出", type="event", importance=0.5)

    raw = {
        "reply": "探してみるね",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "memory_tool_calls": [
            {"type": "memory_search", "query": "海の話"},
            {"type": "memory_get", "memory_id": mid},
        ],
    }
    result = process_report(
        raw,
        emotion=EmotionState(),
        relationship=RelationshipState(),
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        memory_store=store,
    )

    assert result.report.reply == "探してみるね"
    assert result.memory_tool_outcome is not None
    assert len(result.memory_tool_outcome.executed) == 2
    search_result = result.memory_tool_outcome.executed[0]
    assert search_result["tool"] == "memory_search"
    assert search_result["results"][0]["content"] == "海に行った思い出"


def test_propose_fact_does_not_write_db() -> None:
    store = _fresh_store()
    before = len(store.facts.list_active_facts())

    outcome = execute_memory_tool_calls(
        [{"type": "propose_fact", "statement": "即時Fact化禁止の提案"}],
        store,
    )

    assert len(outcome.proposals) == 1
    assert len(store.facts.list_active_facts()) == before


def test_broken_tool_calls_discarded_conversation_continues() -> None:
    store = _fresh_store()
    valid, discarded = parse_memory_tool_calls([{"type": "unknown_tool"}, "not-a-dict"])
    assert valid == []
    assert discarded

    raw = {
        "reply": "続けるよ",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "memory_tool_calls": "broken",
    }
    result = process_report(
        raw,
        emotion=EmotionState(),
        relationship=RelationshipState(),
        thresholds=ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
        memory_store=store,
    )
    assert result.report.reply == "続けるよ"


def test_schema_lenient_parsing() -> None:
    valid, discarded = parse_memory_tool_calls_lenient({
        "memory_tool_calls": [{"type": "memory_get", "memory_id": 1}, {"type": "bad"}],
    })
    assert len(valid) == 1
    assert discarded
