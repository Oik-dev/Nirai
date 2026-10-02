"""記憶候補の審査ライン。設計書 §2.5(関所④引用照合), §4.1

引用照合(機械式・文字照合)・重複チェック・1蒸留ジョブあたりの記憶化件数上限。
機微等級の高度な判定・化粧版生成(Ollamaによる査定)は裏方便で行う。
"""

from __future__ import annotations

from dataclasses import dataclass

from serina.brains.contract.schema import Fusen
from serina.core.config import ThresholdsConfig
from serina.core.memory.store import MemoryStore
from serina.core.state.session import SessionState, Turn


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
    matched_turn = _find_matching_turn(quote, session)
    if matched_turn is None:
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
        created_at=matched_turn.ts,
    )
    return ReviewResult(accepted=True, reason="合格", memory_id=memory_id)


def _find_matching_turn(quote: str, session: SessionState) -> Turn | None:
    """関所④: 会話ログからの原文引用が実際のログに文字照合で存在するかを機械的に検査する。

    2026-07-31是正: マッチしたturnそのものを返す（呼び出し元がturn.tsを記憶のcreated_atに
    引き継ぎ、記憶の日付帰属を「蒸留処理を実行した時刻」ではなく「実際に発話した時刻」に
    紐付けるため。日界処理で前日分が未蒸留のまま日記キャッチアップが先に走った場合、
    蒸留完了後の記憶が処理時刻の日付になり材料窓から漏れて空疎な日記を生成する事故の
    根本対応）。tsが無い(後方互換・移行前データ)場合はNoneのままadd_memoryへ渡り、
    従来通り書き込み時刻にフォールバックする。

    2026-07-31是正(completion-review I-B): 断片が日界をまたぐ場合、短い引用が前日側の
    ターンにも部分一致すると、最初の一致を採ると誤って前日のcreated_atで刻まれてしまう
    （created_atが日をまたぐ材料窓判定に使われるようになった今、誤帰属が実害を持つ）。
    最後（最新）の一致を採ることで、同一文言が複数ターンに現れても発話に近い側を優先する。"""
    for turn in reversed(session.turns):
        if quote in turn.text:
            return turn
    return None


def _find_duplicate(content: str, *, store: MemoryStore, dedup_threshold: float) -> bool:
    """重複チェック（§4.3: dedup閾値はツマミ）。新しさ・重要度を混ぜない純粋な関連度で判定する。"""
    nearest = store.nearest_relevance(content)
    if nearest is None:
        return False
    _, relevance = nearest
    return relevance >= dedup_threshold
