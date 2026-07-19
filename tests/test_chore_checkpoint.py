"""Wave 5 §4-2 chore checkpoint と §3.8 会話優先のテスト。"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.distillation import consume_pending_distillation_jobs
from serina.core.chores.idle_policy import should_digest, should_run_idle_chores
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        if "散歩" in text:
            return [1.0, 0.0, 0.0, 0.0]
        if "コーヒー" in text:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4,
    )


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(fusen_confidence={"default": 0.5, "記憶候補": 0.6}, mood_guard_max_delta_per_turn=0.1)


def _two_candidate_response() -> str:
    return json.dumps({
        "candidates": [
            {
                "quote": "最近散歩が好きなんだ",
                "content": "散歩が好きだという話",
                "type": "fact",
                "importance": 0.6,
                "confidence": 0.9,
            },
            {
                "quote": "コーヒーも好きなんだ",
                "content": "コーヒーが好きだという話",
                "type": "fact",
                "importance": 0.6,
                "confidence": 0.9,
            },
        ]
    })


def test_should_run_idle_chores_false_during_active_session() -> None:
    assert should_run_idle_chores(session_ended=False) is False
    assert should_run_idle_chores(session_ended=True) is True


def test_should_digest_false_during_active_session_even_with_long_gap() -> None:
    """§3.8: digest_gap 経過だけではセッション継続中は裏方を起動しない。"""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
    assert should_digest(
        now=now,
        last_activity_at=now - timedelta(seconds=9999),
        session_ended=False,
        digest_gap_seconds=60,
    ) is False


def test_distillation_checkpoint_resumes_after_yield() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    job_id = box.enqueue(
        "蒸留",
        lane="local",
        payload={
            "turns": [
                {"speaker": "master", "text": "最近散歩が好きなんだ。コーヒーも好きなんだ"},
                {"speaker": "serina", "text": "いいですね"},
            ]
        },
    )
    llm_calls = {"count": 0}

    def call_fn(prompt: str) -> str:
        llm_calls["count"] += 1
        return _two_candidate_response()

    yield_once = {"checks": 0}

    def yield_check() -> bool:
        yield_once["checks"] += 1
        return yield_once["checks"] >= 3

    first = consume_pending_distillation_jobs(
        box,
        memory_store=store,
        thresholds=_thresholds(),
        lane_call_fns={"local": call_fn},
        limit=1,
        yield_check=yield_check,
    )
    assert first.processed == []
    assert len(box.pending(kind="蒸留")) == 1
    assert box.get_checkpoint_processed_ids("distillation", str(job_id)) == {"0"}

    second = consume_pending_distillation_jobs(
        box,
        memory_store=store,
        thresholds=_thresholds(),
        lane_call_fns={"local": call_fn},
        limit=1,
    )
    assert len(second.processed) == 1
    assert second.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    assert box.get_checkpoint_processed_ids("distillation", str(job_id)) == set()
    assert llm_calls["count"] == 2
