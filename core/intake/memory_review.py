"""記憶候補の審査ライン。設計書 §2.5(関所④引用照合), §4.1

引用照合(機械式・文字照合)・重複チェック・1蒸留ジョブあたりの記憶化件数上限。
機微等級の高度な判定・化粧版生成(Qwenによる査定)は裏方便で行う。
"""

from __future__ import annotations

from dataclasses import dataclass

from serina.brains.contract.schema import Fusen
from serina.core.config import ThresholdsConfig
from serina.core.memory.store import MemoryStore
from serina.core.state.session import SessionState


@dataclass(frozen=True)
class ReviewResult:
    accepted: bool
    reason: str
    memory_id: int | None = None


def review_candidate(
    fusen: Fusen,
    *,
    session: SessionState,
    store: MemoryStore,
    thresholds: ThresholdsConfig,
    job_candidate_count: int,
) -> ReviewResult:
    """記憶候補の付箋を関所④・重複チェック・上限チェックに通し、合格ならDBへ書き込む。"""
    if job_candidate_count >= thresholds.memory_max_candidates_per_job:
        return ReviewResult(accepted=False, reason="蒸留ジョブ上限到達")

    quote = fusen.content.get("quote", "")
    content = fusen.content.get("content", "")
    # 2026-07-12監査C-1: LLMが文字列importance・数値quote等を返してもクラッシュさせず棄却する
    if not isinstance(quote, str) or not isinstance(content, str):
        return ReviewResult(accepted=False, reason="不正な候補形式")
    raw_importance = fusen.content.get("importance", 0.5)
    try:
        importance = float(raw_importance)
    except (TypeError, ValueError):
        return ReviewResult(accepted=False, reason="不正な候補形式")
    # serina-code-reviewer 2026-07-13 Important指摘(I-2): LLM申告のimportanceは値域検証が
    # 無く、幻覚した異常値(999等)がrecallスコアに恒久的に乗ってしまう。0-1へ強制クリップする。
    importance = max(0.0, min(1.0, importance))

    if not quote or len(quote) < thresholds.memory_min_quote_length:
        return ReviewResult(accepted=False, reason="引用が短すぎる")
    if not _quote_exists_in_session(quote, session):
        return ReviewResult(accepted=False, reason="引用照合失敗")

    duplicate = _find_duplicate(content, store=store, dedup_threshold=thresholds.memory_dedup_threshold)
    if duplicate:
        return ReviewResult(accepted=False, reason="重複")

    raw_type = fusen.content.get("type", "semantic")
    mem_type = raw_type if isinstance(raw_type, str) else "semantic"
    try:
        sensitivity_grade = int(fusen.content.get("sensitivity_grade", 2))
    except (TypeError, ValueError):
        return ReviewResult(accepted=False, reason="不正な候補形式")

    memory_id = store.add_memory(
        content,
        type=mem_type,
        importance=importance,
        sensitivity_grade=sensitivity_grade,
        protection_grade="B",
        metadata_obj={"source_quotes": [quote]},
    )
    return ReviewResult(accepted=True, reason="合格", memory_id=memory_id)


def _quote_exists_in_session(quote: str, session: SessionState) -> bool:
    """関所④: 会話ログからの原文引用が実際のログに文字照合で存在するかを機械的に検査する。"""
    return any(quote in turn.text for turn in session.turns)


def _find_duplicate(content: str, *, store: MemoryStore, dedup_threshold: float) -> bool:
    """重複チェック（§4.3: dedup閾値はツマミ）。新しさ・重要度を混ぜない純粋な関連度で判定する。"""
    nearest = store.nearest_relevance(content)
    if nearest is None:
        return False
    _, relevance = nearest
    return relevance >= dedup_threshold
