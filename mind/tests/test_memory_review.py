"""記憶候補の審査ラインのテスト。設計書 §2.5(関所④引用照合), §4.1

引用照合(機械式)・重複チェック・1蒸留ジョブ記憶化件数上限 → 合格でDB書き込み。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.contract.schema import Fusen
from serina.core.config import ThresholdsConfig
from serina.core.intake.memory_review import review_candidate
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from serina.core.state.session import SessionState, Turn


def _fake_embedder() -> OllamaEmbedder:
    vectors = {
        "天": [1.0, 0.0, 0.0, 0.0],
        "海": [0.0, 1.0, 0.0, 0.0],
    }

    def call_fn(model: str, text: str) -> list[float]:
        return vectors.get(text[0], [0.0, 0.0, 0.0, 1.0])

    return OllamaEmbedder(call_fn=call_fn)


def _fresh_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    return MemoryStore(str(db_path), embedder=_fake_embedder(), vector_dim=4)


def _thresholds(**overrides) -> ThresholdsConfig:
    base = dict(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        memory_dedup_threshold=0.92,
        memory_max_candidates_per_job=2,
        memory_min_quote_length=8,
    )
    base.update(overrides)
    return ThresholdsConfig(**base)


def _candidate_fusen(content: str, quote: str) -> Fusen:
    return Fusen(
        kind="記憶候補",
        version=1,
        content={
            "content": content,
            "type": "fact",
            "importance": 0.6,
            "sensitivity_grade": 2,
            "quote": quote,
        },
        confidence=0.8,
    )


def _session_with(turn_text: str, ts: str | None = None) -> SessionState:
    session = SessionState()
    session.add_turn(Turn(speaker="master", text=turn_text, ts=ts))
    return session


def test_candidate_with_verifiable_quote_is_accepted() -> None:
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    fusen = _candidate_fusen("天気の良い日に散歩した思い出", quote="天気の良い日に散歩した")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert result.accepted
    assert result.memory_id is not None
    recalled = store.recall("天気の話題", top_k=1)
    assert recalled[0].id == result.memory_id


def test_candidate_without_matching_quote_is_rejected() -> None:
    """関所④: 引用がログに存在しない候補は問答無用で棄却（LLMの捏造を機械で弾く）"""
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    fusen = _candidate_fusen("捏造された記憶", quote="存在しない発言の引用")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert not result.accepted
    assert result.reason == "引用照合失敗"
    assert result.memory_id is None


def test_duplicate_candidate_is_rejected() -> None:
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    store.add_memory("天気の良い日に散歩した思い出", type="fact", importance=0.6)

    fusen = _candidate_fusen("天気の良い日に散歩した思い出（再掲）", quote="天気の良い日に散歩した")
    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert not result.accepted
    assert result.reason == "重複"


def test_job_cap_rejects_beyond_limit() -> None:
    """§2.5: 1蒸留ジョブあたりの記憶化件数に上限"""
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    fusen = _candidate_fusen("天気の話", quote="天気の良い日に散歩した")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(memory_max_candidates_per_job=2),
        job_candidate_count=2,
    )

    assert not result.accepted
    assert result.reason == "蒸留ジョブ上限到達"


def test_short_quote_is_rejected_even_if_substring_matches() -> None:
    """I-3: 極端に短い引用はほぼ全ターンに部分一致してしまうため、最低文字数未満は問答無用で棄却する"""
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    fusen = _candidate_fusen("捏造された記憶", quote="た")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(memory_min_quote_length=8),
        job_candidate_count=0,
    )

    assert not result.accepted
    assert result.reason == "引用が短すぎる"
    assert result.memory_id is None


def test_out_of_range_importance_is_clamped_not_rejected() -> None:
    """I-2: LLM申告のimportanceが幻覚で範囲外(例:999)でも、棄却せず0-1へ丸めて受理する"""
    session = _session_with("天気の良い日に散歩した")
    store = _fresh_store()
    fusen = Fusen(
        kind="記憶候補",
        version=1,
        content={
            "content": "天気の良い日に散歩した思い出",
            "type": "fact",
            "importance": 999.0,
            "sensitivity_grade": 2,
            "quote": "天気の良い日に散歩した",
        },
        confidence=0.8,
    )

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert result.accepted
    assert result.memory_id is not None
    # serina-code-reviewer 2026-07-13再レビュー指摘: accepted判定だけではクリップの
    # 有無を検知できない（クリップを消してもGREENのまま）。実際に1.0へ丸まったかを検証する。
    recalled = store.recall("天気の話題", top_k=1)
    assert recalled[0].id == result.memory_id
    assert recalled[0].importance == 1.0


def test_created_at_uses_matched_turn_timestamp() -> None:
    """2026-07-31是正: 記憶の日付帰属は「蒸留処理を実行した時刻」ではなく「発話時刻」に
    紐付ける（日界処理で前日分が未蒸留のまま日記キャッチアップが先に走ると、蒸留後の記憶が
    処理時刻の日付になり材料窓から漏れて空疎な日記を生成する事故の根本対応）。"""
    turn_ts = "2026-07-30T17:00:00+00:00"
    session = _session_with("天気の良い日に散歩した", ts=turn_ts)
    store = _fresh_store()
    fusen = _candidate_fusen("天気の良い日に散歩した思い出", quote="天気の良い日に散歩した")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert result.accepted
    recalled = store.recall("天気の話題", top_k=1)
    assert recalled[0].id == result.memory_id
    assert recalled[0].created_at == turn_ts


def test_created_at_falls_back_to_write_time_when_turn_has_no_timestamp() -> None:
    """後方互換: ts未設定(移行前データ・旧形式の宿題)でもクラッシュせず、従来通り
    書き込み時刻にフォールバックする。"""
    session = _session_with("天気の良い日に散歩した", ts=None)
    store = _fresh_store()
    fusen = _candidate_fusen("天気の良い日に散歩した思い出", quote="天気の良い日に散歩した")

    result = review_candidate(
        fusen, session=session, store=store, thresholds=_thresholds(), job_candidate_count=0,
    )

    assert result.accepted
    recalled = store.recall("天気の話題", top_k=1)
    assert recalled[0].created_at  # 書き込み時刻(DB既定)が入っており空でない


def main() -> None:
    tests = [
        test_candidate_with_verifiable_quote_is_accepted,
        test_candidate_without_matching_quote_is_rejected,
        test_duplicate_candidate_is_rejected,
        test_job_cap_rejects_beyond_limit,
        test_short_quote_is_rejected_even_if_substring_matches,
        test_out_of_range_importance_is_clamped_not_rejected,
        test_created_at_uses_matched_turn_timestamp,
        test_created_at_falls_back_to_write_time_when_turn_has_no_timestamp,
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
