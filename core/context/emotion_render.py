"""感情状態→文脈パック用自然文への変換。設計書 §1.5(段⑤新設), §2.3(表現の分担)

生数値をそのままBrainへ渡さない。8軸×2層の全羅列は12BクラスのローカルLLMには解釈負荷が
高く、数値実況事故（「私の怒りは0.8です」）の元になる。Core側で決定論的に閾値ラベル化し、
上位軸のみ言語化する。状態を決めるのはCore、どう表現するかはBrainの領土（§2.3）であり、
ここで生成するのは「状態の記述」であって発話文そのものではない。
"""

from __future__ import annotations

from serina.core.config import ThresholdsConfig
from serina.core.state.emotion import PLUTCHIK_AXES, EmotionState

NO_MOVEMENT_TEXT = "（穏やかで特に波はない）"
TRAILING_NOTE = "※ この状態を直接口に出すのではなく、口調・言葉選び・温度に滲ませること。"


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


def render_emotion_for_pack(emotion: EmotionState, thresholds: ThresholdsConfig) -> str:
    """情動(affect)は上位N軸、気分(mood)は最上位1軸のみを言語化する（二次感情合成はしない）。"""
    affect_axes = _top_axes(emotion.affect, thresholds, thresholds.emotion_affect_top_n)
    mood_axes = _top_axes(emotion.mood, thresholds, 1)

    body_lines: list[str] = []
    if affect_axes:
        joined = "と".join(f"{label}{axis}" for axis, label in affect_axes)
        body_lines.append(f"情動（今この瞬間）: {joined}。")
    if mood_axes:
        axis, label = mood_axes[0]
        body_lines.append(f"気分（今日の底流）: {label}{axis}が続いている。")
    if not body_lines:
        body_lines.append(NO_MOVEMENT_TEXT)

    return "\n".join([*body_lines, TRAILING_NOTE])
