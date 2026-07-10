"""Core本体への記憶接続の結線テスト。設計書v2 §5.4 Phase2「想起（読み）と審査ライン（書き）を接続」"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.memory.embedder import OllamaEmbedder
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.runtime import Core


def _fake_embedder() -> OllamaEmbedder:
    vectors = {"天": [1.0, 0.0, 0.0, 0.0]}

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [0.0, 0.0, 0.0, 1.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        memory_dedup_threshold=0.92,
        memory_max_candidates_per_session=5,
    )


class StubBrain:
    def __init__(self, script: dict) -> None:
        self.script = script
        self.received_pack = None

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.received_pack = pack
        return self.script


def test_turn_recalls_relevant_memory_into_pack() -> None:
    store = _fresh_store()
    store.add_memory("天気がいい日の話", type="fact", importance=0.6, sensitivity_grade=0)

    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), memory_store=store)
    brain = StubBrain({
        "reply": "そうですね",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("天気の話をしよう", brain)

    assert "天気がいい日の話" in brain.received_pack.render()


def test_accepted_memory_candidate_is_written_to_store() -> None:
    store = _fresh_store()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), memory_store=store)
    brain = StubBrain({
        "reply": "覚えておきますね",
        "fusen_list": [
            {
                "kind": "記憶候補",
                "version": 1,
                "content": {
                    "content": "散歩が好きだという話",
                    "type": "fact",
                    "importance": 0.6,
                    "sensitivity_grade": 0,
                    "quote": "散歩が好きなんだ",
                },
                "confidence": 0.9,
            }
        ],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("散歩が好きなんだ", brain)

    recalled = store.recall("散歩の話題", top_k=1)
    assert len(recalled) == 1
    assert recalled[0].content == "散歩が好きだという話"


def main() -> None:
    tests = [
        test_turn_recalls_relevant_memory_into_pack,
        test_accepted_memory_candidate_is_written_to_store,
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
