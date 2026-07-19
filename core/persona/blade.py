"""人格の刃（合意台帳 §3.7 / 設計書 §4.8）。

からかい許容度の明文確認と、安全フィルタ発動時の見えるブレーキ。
"""

from __future__ import annotations

TEASING_TOLERANCE_MARKER = "からかい許容度"

VISIBLE_BRAKE_MODES = frozenset({"none", "parenthetical", "suffix"})


def persona_contains_teasing_tolerance(persona_text: str) -> bool:
    """可変ブロックにからかい許容度の明文が含まれるか。"""
    return TEASING_TOLERANCE_MARKER in persona_text


def apply_visible_brake(text: str, *, mode: str, filtered: bool) -> str:
    """安全フィルタ発動時、無言のすり替えではなく見えるブレーキを付ける。

    mode は thresholds [persona_blade].visible_brake_mode（none / parenthetical / suffix）。
    filtered=False のときは text をそのまま返す。
    """
    if not filtered or mode == "none":
        return text
    if mode == "parenthetical":
        return f"（フィルタ）{text}"
    if mode == "suffix":
        return f"{text}（フィルタ）"
    raise ValueError(f"未知の visible_brake_mode: {mode}")
