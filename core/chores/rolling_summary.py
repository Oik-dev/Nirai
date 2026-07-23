"""転がし要約（rolling_summary / fine_summary）の更新。設計書 §1.4, §1.5。

- fine_summary: 直近 fine_band ターンを細かめ粒度で要約（ターン確定後）
- rolling_summary: fine_band より古い未折り込みターンを N ターン段刻みで粗く追記

LLM失敗時は何も進めず次回再挑戦（distillation.pyと同じ電源断耐性の思想）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from serina.core.config import ThresholdsConfig
from serina.core.state.session import SessionState, Turn

COARSE_SUMMARY_FORMAT_INSTRUCTION = """
以下は今セッションの会話ログの古い断片です。これまでの要約（あれば）に続けて、
大まかな流れだけを短い日本語で追記してください。細部や引用は不要です。
マスターの発言とセリナの発言を混同しないこと。誰が言ったかを代名詞や
省略主語にせず、要約本文の中で毎回明示してください。
説明や前置きは不要です。要約本文のみを返してください。
""".strip()

FINE_SUMMARY_FORMAT_INSTRUCTION = """
以下は今セッションの直近の会話です。話題・具体的内容・双方の言及を残しつつ、
細かめに要約してください。流れだけのぼんやりした要約は避けてください。
マスターの発言とセリナの発言を混同しないこと。誰が言ったかを代名詞や
省略主語にせず、要約本文の中で毎回明示してください。
説明や前置きは不要です。要約本文のみを返してください。
""".strip()


@dataclass(frozen=True)
class SummaryUpdateOutcome:
    updated: bool
    reason: str | None = None


@dataclass(frozen=True)
class TurnSummaryBatchOutcome:
    fine: SummaryUpdateOutcome
    coarse: SummaryUpdateOutcome


def fine_band_turns(session: SessionState, *, band_size: int) -> list[Turn]:
    """直近 fine_band ターン（⑦用）。"""
    if band_size <= 0:
        return []
    return session.turns[-band_size:]


def coarse_overflow_turns(session: SessionState, *, fine_band_turns: int) -> list[Turn]:
    """fine_band より古く、まだ rolling_summary へ折り込んでいないターン。"""
    if fine_band_turns < 0:
        fine_band_turns = 0
    keep_from = max(0, len(session.turns) - fine_band_turns)
    start = min(session.summarized_turn_count, keep_from)
    return session.turns[start:keep_from]


def overflow_turns(session: SessionState, *, window_size: int) -> list[Turn]:
    """互換: 窓の外にあり、まだ要約へ折り込んでいないターン（idle 経路用）。"""
    if window_size < 0:
        window_size = 0
    keep_from = max(0, len(session.turns) - window_size)
    start = min(session.summarized_turn_count, keep_from)
    return session.turns[start:keep_from]


# 要約LLMへ渡す話者ラベルの日本語化。英語ラベルのまま渡すと話者取り違えの
# 一因になるため（マスター相談 2026-07-23）、要約プロンプト内だけ日本語表記に揃える。
_SPEAKER_LABELS_JA = {"master": "マスター", "serina": "セリナ"}


def _speaker_label(speaker: str) -> str:
    return _SPEAKER_LABELS_JA.get(speaker, speaker)


def build_coarse_summary_prompt(*, existing_summary: str, turns: list[Turn]) -> str:
    lines = "\n".join(f"{_speaker_label(t.speaker)}: {t.text}" for t in turns)
    prior = existing_summary.strip() or "（まだ要約なし）"
    return (
        f"【これまでの要約】\n{prior}\n\n"
        f"【今回折り込む会話】\n{lines}\n\n"
        f"{COARSE_SUMMARY_FORMAT_INSTRUCTION}"
    )


def build_fine_summary_prompt(*, turns: list[Turn]) -> str:
    lines = "\n".join(f"{_speaker_label(t.speaker)}: {t.text}" for t in turns)
    return f"【直近の会話】\n{lines}\n\n{FINE_SUMMARY_FORMAT_INSTRUCTION}"


def build_summary_prompt(*, existing_summary: str, turns: list[Turn]) -> str:
    """互換: 粗い要約プロンプト。"""
    return build_coarse_summary_prompt(existing_summary=existing_summary, turns=turns)


def update_fine_summary(
    session: SessionState,
    *,
    call_fn: Callable[[str], str],
    band_size: int,
) -> SummaryUpdateOutcome:
    """直近 band を細かめ要約して fine_summary へ上書き。"""
    band = fine_band_turns(session, band_size=band_size)
    if not band:
        return SummaryUpdateOutcome(updated=False, reason="直近帯ターンなし")

    prompt = build_fine_summary_prompt(turns=band)
    try:
        new_summary = call_fn(prompt).strip()
    except Exception:  # noqa: BLE001
        return SummaryUpdateOutcome(updated=False, reason="LLM呼び出し失敗")

    if not new_summary:
        return SummaryUpdateOutcome(updated=False, reason="空応答")

    session.fine_summary = new_summary
    return SummaryUpdateOutcome(updated=True)


def update_coarse_rolling_summary(
    session: SessionState,
    *,
    call_fn: Callable[[str], str],
    fine_band_turns: int,
    step_turns: int,
) -> SummaryUpdateOutcome:
    """未折り込みが step_turns 以上なら、先頭 step 分を1回だけ粗く追記。"""
    if step_turns <= 0:
        return SummaryUpdateOutcome(updated=False, reason="段刻み無効")

    overflow = coarse_overflow_turns(session, fine_band_turns=fine_band_turns)
    if len(overflow) < step_turns:
        return SummaryUpdateOutcome(updated=False, reason="段刻み未達")

    batch = overflow[:step_turns]
    prompt = build_coarse_summary_prompt(existing_summary=session.rolling_summary, turns=batch)
    try:
        new_summary = call_fn(prompt).strip()
    except Exception:  # noqa: BLE001
        return SummaryUpdateOutcome(updated=False, reason="LLM呼び出し失敗")

    if not new_summary:
        return SummaryUpdateOutcome(updated=False, reason="空応答")

    session.rolling_summary = new_summary
    session.summarized_turn_count += len(batch)
    return SummaryUpdateOutcome(updated=True)


def update_rolling_summary(
    session: SessionState,
    *,
    call_fn: Callable[[str], str],
    window_size: int,
) -> SummaryUpdateOutcome:
    """互換: idle 経路。窓から溢れた未要約ターンがあれば1回要約して追記。"""
    overflow = overflow_turns(session, window_size=window_size)
    if not overflow:
        return SummaryUpdateOutcome(updated=False, reason="溢れたターンなし")

    prompt = build_coarse_summary_prompt(existing_summary=session.rolling_summary, turns=overflow)
    try:
        new_summary = call_fn(prompt).strip()
    except Exception:  # noqa: BLE001
        return SummaryUpdateOutcome(updated=False, reason="LLM呼び出し失敗")

    if not new_summary:
        return SummaryUpdateOutcome(updated=False, reason="空応答")

    session.rolling_summary = new_summary
    session.summarized_turn_count = max(0, len(session.turns) - window_size)
    return SummaryUpdateOutcome(updated=True)


def update_turn_summaries(
    session: SessionState,
    *,
    call_fn: Callable[[str], str],
    thresholds: ThresholdsConfig,
) -> TurnSummaryBatchOutcome:
    """ターン確定後: fine を更新し、条件を満たせば coarse を1回だけ更新。"""
    fine_outcome = update_fine_summary(
        session,
        call_fn=call_fn,
        band_size=thresholds.fine_band_turns,
    )
    coarse_outcome = update_coarse_rolling_summary(
        session,
        call_fn=call_fn,
        fine_band_turns=thresholds.fine_band_turns,
        step_turns=thresholds.coarse_update_every_n_turns,
    )
    return TurnSummaryBatchOutcome(fine=fine_outcome, coarse=coarse_outcome)
