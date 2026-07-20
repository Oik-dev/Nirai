"""感情状態→文脈パック用自然文への変換。設計書 §1.5(段⑤新設), §2.3(表現の分担)

生数値をそのままBrainへ渡さない。Core側で決定論的に閾値ラベル化し、上位軸と
一次ダイアド（二次感情）最大1つを言語化する。
"""

from __future__ import annotations

from serina.core.config import ThresholdsConfig
from serina.core.state.emotion import PLUTCHIK_AXES, EmotionState

NO_MOVEMENT_TEXT = "（穏やかで特に波はない）"
TRAILING_NOTE = "※ この状態を直接口に出すのではなく、口調・言葉選び・温度に滲ませること。"

# プルチック一次ダイアド（隣接ペアのみ）。設計書 §2.3。
PRIMARY_DYADS: tuple[tuple[str, str, str], ...] = (
    ("喜び", "信頼", "愛情"),
    ("信頼", "恐れ", "服従"),
    ("恐れ", "驚き", "警戒"),
    ("驚き", "悲しみ", "失望"),
    ("悲しみ", "嫌悪", "後悔"),
    ("嫌悪", "怒り", "軽蔑"),
    ("怒り", "期待", "攻撃性"),
    ("期待", "喜び", "楽観"),
)


def _label(value: float, thresholds: ThresholdsConfig) -> str | None:
    if value < thresholds.emotion_ignore_below:
        return None
    if value < thresholds.emotion_mild_below:
        return "軽い"
    if value < thresholds.emotion_strong_below:
        return "はっきりした"
    return "強い"


def _top_axes(
    values: dict[str, float], thresholds: ThresholdsConfig, top_n: int,
) -> list[tuple[str, str]]:
    labeled: list[tuple[str, str, float]] = []
    for axis in PLUTCHIK_AXES:
        value = values.get(axis, 0.0)
        label = _label(value, thresholds)
        if label is not None:
            labeled.append((axis, label, value))
    labeled.sort(key=lambda item: item[2], reverse=True)
    return [(axis, label) for axis, label, _ in labeled[:top_n]]


def _best_dyad(
    affect: dict[str, float], thresholds: ThresholdsConfig,
) -> tuple[str, str] | None:
    """両軸がラベル可能かつ dyad_min 以上の一次ダイアドのうち、min強度が最大のものを1つ返す。"""
    floor = thresholds.emotion_dyad_min
    best: tuple[str, str, float] | None = None
    for a, b, name in PRIMARY_DYADS:
        v1 = affect.get(a, 0.0)
        v2 = affect.get(b, 0.0)
        if _label(v1, thresholds) is None or _label(v2, thresholds) is None:
            continue
        strength = min(v1, v2)
        if strength < floor:
            continue
        if best is None or strength > best[2]:
            label = _label(strength, thresholds) or "軽い"
            best = (name, label, strength)
    if best is None:
        return None
    return best[0], best[1]


def render_emotion_for_pack(emotion: EmotionState, thresholds: ThresholdsConfig) -> str:
    """情動・気分の上位軸＋一次ダイアド（二次感情）最大1つを言語化する。"""
    affect_axes = _top_axes(emotion.affect, thresholds, thresholds.emotion_affect_top_n)
    mood_axes = _top_axes(emotion.mood, thresholds, 1)

    body_lines: list[str] = []
    if affect_axes:
        joined = "と".join(f"{label}{axis}" for axis, label in affect_axes)
        body_lines.append(f"情動（今この瞬間）: {joined}。")
    if mood_axes:
        axis, label = mood_axes[0]
        body_lines.append(f"気分（今日の底流）: {label}{axis}が続いている。")
    dyad = _best_dyad(emotion.affect, thresholds)
    if dyad is not None:
        name, label = dyad
        body_lines.append(f"二次感情: {label}{name}。")
    if not body_lines:
        body_lines.append(NO_MOVEMENT_TEXT)

    return "\n".join([*body_lines, TRAILING_NOTE])
