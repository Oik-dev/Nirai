"""記憶候補の審査ライン。設計書v2 §2.5(関所④引用照合), §4.1

Phase2範囲: 引用照合(機械式・文字照合)・重複チェック・1セッション記憶化件数上限。
機微等級の高度な判定・化粧版生成(Auroraによる査定)は裏方便(Phase4)で行う。
"""

from __future__ import annotations

from dataclasses import dataclass

from serina.brains.contract.schema import Fusen
from serina.core_v2.config import ThresholdsConfig
from serina.core_v2.memory.store import MemoryStore
from serina.core_v2.state.session import SessionState


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
    session_candidate_count: int,
) -> ReviewResult:
    """記憶候補の付箋を関所④・重複チェック・上限チェックに通し、合格ならDBへ書き込む。"""
    if session_candidate_count >= thresholds.memory_max_candidates_per_session:
        return ReviewResult(accepted=False, reason="セッション上限到達")

    quote = fusen.content.get("quote", "")
    if not quote or not _quote_exists_in_session(quote, session):
        return ReviewResult(accepted=False, reason="引用照合失敗")

    content = fusen.content.get("content", "")
    duplicate = _find_duplicate(content, store=store, dedup_threshold=thresholds.memory_dedup_threshold)
    if duplicate:
        return ReviewResult(accepted=False, reason="重複")

    memory_id = store.add_memory(
        content,
        type=fusen.content.get("type", "fact"),
        importance=float(fusen.content.get("importance", 0.5)),
        sensitivity_grade=int(fusen.content.get("sensitivity_grade", 2)),
        protection_grade="B",
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
