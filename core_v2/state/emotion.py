"""感情状態: プルチック8軸×情動/気分二層。設計書v2 §2.3, §2.6"""

from __future__ import annotations

PLUTCHIK_AXES: tuple[str, ...] = (
    "喜び",
    "信頼",
    "恐れ",
    "驚き",
    "悲しみ",
    "嫌悪",
    "怒り",
    "期待",
)

_MIN_VALUE = 0.0
_MAX_VALUE = 1.0


def _clamp(value: float) -> float:
    return max(_MIN_VALUE, min(_MAX_VALUE, value))


class EmotionState:
    """情動（速い層・変化制限なし）と気分（遅い層・急変防止弁つき）を保持する。"""

    def __init__(self) -> None:
        self.affect: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}
        self.mood: dict[str, float] = {axis: 0.0 for axis in PLUTCHIK_AXES}

    def apply_affect_delta(self, deltas: dict[str, float]) -> None:
        """情動層を更新する。1ターンで振り切ってよく、変化幅の制限はない（§2.3）。"""
        for axis, delta in deltas.items():
            if axis not in self.affect:
                raise KeyError(f"未知のプルチック軸: {axis}")
            self.affect[axis] = _clamp(self.affect[axis] + delta)

    def apply_mood_delta(self, deltas: dict[str, float], max_delta_per_turn: float) -> None:
        """気分層を更新する。急変防止弁により1ターンあたりの変化幅を制限する（§2.3）。"""
        for axis, delta in deltas.items():
            if axis not in self.mood:
                raise KeyError(f"未知のプルチック軸: {axis}")
            guarded_delta = max(-max_delta_per_turn, min(max_delta_per_turn, delta))
            self.mood[axis] = _clamp(self.mood[axis] + guarded_delta)
