"""転がし要約（rolling_summary）の更新。設計書v2 §1.4。

直近会話の窓から溢れた古いターンを、アイドル時にlocal(Aurora)へ要約発注して
SessionState.rolling_summaryへ追記する。会話中は古い要約のままで許容し、
セッション終了（SessionState再生成）でリセットされる。

LLM失敗時は何も進めず次回再挑戦（distillation.pyと同じ電源断耐性の思想）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from serina.core_v2.state.session import SessionState, Turn

SUMMARY_FORMAT_INSTRUCTION = """
以下は今セッションの会話ログの断片です。これまでの要約（あれば）に続けて、
この断片の要点だけを短い日本語で追記してください。説明や前置きは不要です。
要約本文のみを返してください。
""".strip()


@dataclass(frozen=True)
class SummaryUpdateOutcome:
    updated: bool
    reason: str | None = None


def overflow_turns(session: SessionState, *, window_size: int) -> list[Turn]:
    """窓の外にあり、まだ要約へ折り込んでいないターンを返す。"""
    if window_size < 0:
        window_size = 0
    keep_from = max(0, len(session.turns) - window_size)
    start = min(session.summarized_turn_count, keep_from)
    return session.turns[start:keep_from]


def build_summary_prompt(*, existing_summary: str, turns: list[Turn]) -> str:
    lines = "\n".join(f"{t.speaker}: {t.text}" for t in turns)
    prior = existing_summary.strip() or "（まだ要約なし）"
    return (
        f"【これまでの要約】\n{prior}\n\n"
        f"【今回折り込む会話】\n{lines}\n\n"
        f"{SUMMARY_FORMAT_INSTRUCTION}"
    )


def update_rolling_summary(
    session: SessionState,
    *,
    call_fn: Callable[[str], str],
    window_size: int,
) -> SummaryUpdateOutcome:
    """窓から溢れた未要約ターンがあれば1回要約して追記する。"""
    overflow = overflow_turns(session, window_size=window_size)
    if not overflow:
        return SummaryUpdateOutcome(updated=False, reason="溢れたターンなし")

    prompt = build_summary_prompt(existing_summary=session.rolling_summary, turns=overflow)
    try:
        new_summary = call_fn(prompt).strip()
    except Exception:  # noqa: BLE001
        return SummaryUpdateOutcome(updated=False, reason="LLM呼び出し失敗")

    if not new_summary:
        return SummaryUpdateOutcome(updated=False, reason="空応答")

    session.rolling_summary = new_summary
    session.summarized_turn_count = max(
        0, len(session.turns) - window_size,
    )
    return SummaryUpdateOutcome(updated=True)
