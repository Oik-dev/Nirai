"""蒸留ジョブの消化ロジックのテスト。設計書v2 §2.4(二車線), §4.1(記憶の一生)。

LLM不要（call_fnをスタブ化）。DECISIONS 2026-07-11「蒸留=記憶候補の唯一の生成源」を
実装した消化ロジック(core_v2/chores/distillation.py)の結線を検査する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.chores.chore_box import ChoreBox
from serina.core_v2.chores.distillation import (
    build_distillation_prompt,
    consume_pending_distillation_jobs,
)
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.memory.embedder import OllamaEmbedder
from serina.core_v2.memory.protection import ChangeLog
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.routing.quota_ledger import QuotaLedger, QuotaSpec


def _fake_embedder() -> OllamaEmbedder:
    def call_fn(model: str, text: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0] if "散歩" in text else [0.0, 0.0, 0.0, 1.0]

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "記憶候補": 0.6},
        mood_guard_max_delta_per_turn=0.1,
        memory_dedup_threshold=0.92,
        memory_max_candidates_per_session=5,
    )


def _turns() -> list[dict]:
    return [
        {"speaker": "master", "text": "最近散歩が好きなんだ"},
        {"speaker": "serina", "text": "いいですね"},
    ]


def test_build_distillation_prompt_includes_turns_and_format() -> None:
    prompt = build_distillation_prompt(_turns())
    assert "master: 最近散歩が好きなんだ" in prompt
    assert "candidates" in prompt


def test_accepted_candidate_written_to_store_and_job_marked_done() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})

    def call_fn(prompt: str) -> str:
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

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": call_fn},
    )

    assert summary.total_accepted == 1
    assert box.pending(kind="蒸留") == []
    recalled = store.recall("散歩の話題", top_k=1)
    assert len(recalled) == 1
    assert recalled[0].content == "散歩が好きだという話"
    assert recalled[0].sensitivity_grade == 2


def test_candidate_with_quote_not_in_turns_is_rejected() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})

    def call_fn(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"quote": "でっちあげの引用", "content": "捏造された記憶", "confidence": 0.9}
            ]
        })

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": call_fn},
    )

    assert summary.total_accepted == 0
    assert summary.processed[0].rejected == ["引用照合失敗"]
    assert box.pending(kind="蒸留") == []  # ジョブ自体は処理済み(棄却は正常な審査結果)


def test_low_confidence_candidate_is_rejected() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})

    def call_fn(prompt: str) -> str:
        return json.dumps({
            "candidates": [
                {"quote": "最近散歩が好きなんだ", "content": "散歩の話", "confidence": 0.1}
            ]
        })

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": call_fn},
    )

    assert summary.total_accepted == 0
    assert summary.processed[0].rejected == ["確信度不足"]


def test_job_with_unavailable_lane_is_left_pending() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="cloud", payload={"turns": _turns()})

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": lambda p: "{}"},
    )

    assert summary.skipped_no_lane != []
    assert len(box.pending(kind="蒸留")) == 1


def test_job_where_llm_call_raises_is_left_pending_for_retry() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})

    def failing_call_fn(prompt: str) -> str:
        raise RuntimeError("通信エラー")

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": failing_call_fn},
    )

    assert summary.failed != []
    assert len(box.pending(kind="蒸留")) == 1


def test_job_where_response_is_not_valid_json_is_left_pending() -> None:
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(), lane_call_fns={"local": lambda p: "not json at all"},
    )

    assert summary.failed != []
    assert len(box.pending(kind="蒸留")) == 1


def test_batch_candidate_limit_enforced_across_jobs() -> None:
    box = _fresh_chore_box()
    # 3件それぞれ別ベクトルを返す埋め込み器（重複チェックで潰されないようにするため。
    # 定数ベクトルだと2件目以降が「重複」で棄却され、本来テストしたい
    # 「セッション上限到達」に到達する前に落ちてしまう）。
    distinct_vectors = {
        "記憶0": [1.0, 0.0, 0.0, 0.0],
        "記憶1": [0.0, 1.0, 0.0, 0.0],
        "記憶2": [0.0, 0.0, 1.0, 0.0],
    }

    def embed_call_fn(model: str, text: str) -> list[float]:
        return distinct_vectors.get(text, [0.0, 0.0, 0.0, 1.0])

    store = MemoryStore(
        str(Path(tempfile.mkdtemp()) / "test_memory.db"),
        embedder=OllamaEmbedder(call_fn=embed_call_fn),
        vector_dim=4,
    )
    for i in range(3):
        box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": f"話題{i}"}]})

    call_count = {"n": 0}

    def call_fn(prompt: str) -> str:
        call_count["n"] += 1
        idx = call_count["n"] - 1
        return json.dumps({
            "candidates": [
                {"quote": f"話題{idx}", "content": f"記憶{idx}", "confidence": 0.9}
            ]
        })

    thresholds = ThresholdsConfig(
        fusen_confidence={"default": 0.5, "記憶候補": 0.6},
        mood_guard_max_delta_per_turn=0.1,
        memory_max_candidates_per_session=2,
    )

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=thresholds, lane_call_fns={"local": call_fn},
    )

    assert summary.total_accepted == 2
    rejections = [r for outcome in summary.processed for r in outcome.rejected]
    assert "セッション上限到達" in rejections


def test_job_switches_lane_to_local_after_three_failures() -> None:
    """2026-07-12決定: 毒饅頭ジョブの先頭詰まり対策。3回連続失敗したcloud車線ジョブは
    localへ振替し、失敗回数もリセットされる（APIキー喪失時の永久pendingもこの機構で解消）。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    job_id = box.enqueue("蒸留", lane="cloud", payload={"turns": _turns()})

    def failing_call_fn(prompt: str) -> str:
        raise RuntimeError("cloud通信エラー")

    for _ in range(3):
        consume_pending_distillation_jobs(
            box, memory_store=store, thresholds=_thresholds(),
            lane_call_fns={"cloud": failing_call_fn, "local": lambda p: "{}"},
        )

    job = box.pending()[0]
    assert job.id == job_id
    assert job.lane == "local"
    assert job.failure_count == 0


def test_job_shelved_after_three_failures_with_no_lane_switch_available() -> None:
    """local車線しか無い（またはcloud→local振替後も失敗し続ける）場合は棚上げ棚へ移動し、
    change_logへ日本語レポートを残す（原則1: 無言破棄禁止）。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    box.enqueue("蒸留", lane="local", payload={"turns": _turns()})
    change_log = ChangeLog(Path(tempfile.mkdtemp()) / "changes.jsonl")

    def failing_call_fn(prompt: str) -> str:
        raise RuntimeError("通信エラー")

    for _ in range(3):
        summary = consume_pending_distillation_jobs(
            box, memory_store=store, thresholds=_thresholds(),
            lane_call_fns={"local": failing_call_fn}, change_log=change_log,
        )

    assert box.pending(kind="蒸留") == []
    assert box.shelved_count() == 1
    assert summary.shelved != []
    reports = change_log.read_all()
    assert any(r.action == "蒸留ジョブ棚上げ" for r in reports)


def test_cloud_job_skipped_without_failure_when_quota_exhausted() -> None:
    """quota由来のスキップは「壊れたジョブ」ではないため失敗回数にカウントしない
    （advisorレビュー2026-07-12: quota-block ≠ 失敗）。"""
    box = _fresh_chore_box()
    store = _fresh_store()
    job_id = box.enqueue("蒸留", lane="cloud", payload={"turns": _turns()})
    quota_ledger = QuotaLedger()
    quota = QuotaSpec(name="gemini_flash_lite", daily_quota=0, per_minute_quota=-1)

    def never_called(prompt: str) -> str:
        raise AssertionError("quota切れならcall_fnは呼ばれないはず")

    summary = consume_pending_distillation_jobs(
        box, memory_store=store, thresholds=_thresholds(),
        lane_call_fns={"cloud": never_called}, quota_ledger=quota_ledger, cloud_quota=quota,
    )

    assert summary.skipped_quota == [job_id]
    job = box.pending()[0]
    assert job.failure_count == 0


def main() -> None:
    tests = [
        test_build_distillation_prompt_includes_turns_and_format,
        test_accepted_candidate_written_to_store_and_job_marked_done,
        test_candidate_with_quote_not_in_turns_is_rejected,
        test_low_confidence_candidate_is_rejected,
        test_job_with_unavailable_lane_is_left_pending,
        test_job_where_llm_call_raises_is_left_pending_for_retry,
        test_job_where_response_is_not_valid_json_is_left_pending,
        test_batch_candidate_limit_enforced_across_jobs,
        test_job_switches_lane_to_local_after_three_failures,
        test_job_shelved_after_three_failures_with_no_lane_switch_available,
        test_cloud_job_skipped_without_failure_when_quota_exhausted,
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
