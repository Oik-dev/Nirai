"""蒸留消化の表口（トリガー配線）テスト。設計書 §2.4。

core/chores/orchestrator.py の run_startup_chores(③朝礼)・run_session_end_chores(①終了時)
を検査する。LLM不要（call_fnをスタブ化）。DECISIONS 2026-07-11の未解決事項#1
（消化ロジックを実際に呼ぶ表口が無い）を解消したことの結線検査。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.chores.orchestrator import (
    build_default_lane_call_fns,
    run_idle_digest_chunk,
    run_session_end_chores,
    run_startup_chores,
)
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from serina.core.runtime import Core


class StubBrain:
    def __init__(self, script: dict) -> None:
        self.script = script

    def converse(self, pack) -> dict:  # noqa: ANN001
        return self.script


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0] if "散歩" in text else [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"), embedder=_fake_embedder(), vector_dim=4,
    )


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _thresholds(fragment_turns: int = 20) -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "記憶候補": 0.6},
        mood_guard_max_delta_per_turn=0.1,
        chore_fragment_turns=fragment_turns,
    )


def _reply_brain() -> StubBrain:
    return StubBrain({
        "reply": "うん",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })


def _distillation_call_fn(prompt: str) -> str:
    return json.dumps({
        "candidates": [
            {
                "quote": "最近散歩が好きなんだ",
                "content": "散歩が好きだという話",
                "type": "fact",
                "importance": 0.6,
                "confidence": 0.9,
            }
        ]
    })


def test_run_startup_chores_consumes_leftover_pending_job() -> None:
    """③次回起動時の朝礼: 前回の積み残し(pending)を新しい日の残弾で消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [
            {"speaker": "master", "text": "最近散歩が好きなんだ"},
            {"speaker": "serina", "text": "いいですね"},
        ]
    })

    summary = run_startup_chores(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": _distillation_call_fn},
    )

    assert summary.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    recalled = store.recall("散歩の話題", top_k=1)
    assert recalled[0].content == "散歩が好きだという話"


def test_run_session_end_chores_flushes_and_consumes() -> None:
    """①セッション終了時: end_session()で端数を積んでから、閉じる前に消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)
    core.turn("最近散歩が好きなんだ", _reply_brain())

    job_ids, summary = run_session_end_chores(
        core, box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn},
    )

    assert len(job_ids) == 1  # end_session時の端数flush分
    assert summary.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    assert core.session.turns == []  # Core状態は既にリセット済み
    recalled = store.recall("散歩の話題", top_k=1)
    assert recalled[0].content == "散歩が好きだという話"


def test_run_session_end_chores_also_consumes_older_pending_jobs() -> None:
    """①は今回セッションの端数だけでなく、宿題箱に残る過去の積み残しも合わせて消化する。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "前回の積み残し"}]
    })
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)
    core.turn("最近散歩が好きなんだ", _reply_brain())

    job_ids, summary = run_session_end_chores(
        core, box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn},
    )

    assert len(job_ids) == 1  # 新規に積んだのは端数の1件のみ
    assert len(summary.processed) == 2  # だが消化対象は積み残し1件+端数1件=2件


def test_run_idle_digest_chunk_consumes_only_limit_jobs() -> None:
    """②アイドル小分け消化: limit件だけ消化し、残りはpendingのまま残す。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "最近散歩が好きなんだ"}]
    })
    box.enqueue("蒸留", lane="local", payload={
        "turns": [{"speaker": "master", "text": "もうひとつの断片"}]
    })

    summary = run_idle_digest_chunk(
        box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn}, limit=1,
    )

    assert len(summary.processed) == 1
    assert len(box.pending(kind="蒸留")) == 1  # 2件目は次回の機会に持ち越し


def test_run_idle_digest_chunk_noop_when_empty() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()

    summary = run_idle_digest_chunk(
        box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"local": _distillation_call_fn}, limit=1,
    )

    assert summary.processed == []


def test_build_default_lane_call_fns_local_only_without_gemini_key() -> None:
    lane_call_fns = build_default_lane_call_fns()

    assert "local" in lane_call_fns
    assert callable(lane_call_fns["local"])
    assert "cloud" not in lane_call_fns


def test_build_default_lane_call_fns_includes_cloud_with_key() -> None:
    lane_call_fns = build_default_lane_call_fns(gemini_api_key="dummy-key")

    assert "local" in lane_call_fns
    assert "cloud" in lane_call_fns
    assert callable(lane_call_fns["cloud"])


def main() -> None:
    tests = [
        test_run_startup_chores_consumes_leftover_pending_job,
        test_run_session_end_chores_flushes_and_consumes,
        test_run_session_end_chores_also_consumes_older_pending_jobs,
        test_run_idle_digest_chunk_consumes_only_limit_jobs,
        test_run_idle_digest_chunk_noop_when_empty,
        test_build_default_lane_call_fns_local_only_without_gemini_key,
        test_build_default_lane_call_fns_includes_cloud_with_key,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
