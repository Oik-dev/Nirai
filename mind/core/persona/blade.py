"""見えるブレーキ（設計書 §2.9）。

安全フィルタ発動時、無言のすり替えではなく「（フィルタ）」等で可視化する。
（旧「からかい許容度」明文検査は 2026-07-20 退役）
"""

from __future__ import annotations

VISIBLE_BRAKE_MODES = frozenset({"none", "parenthetical", "suffix"})


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
