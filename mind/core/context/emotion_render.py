"""感情状態→文脈パック用自然文への変換。設計書 §1.5(段⑤), §2.3(表現の分担)

生数値をそのままBrainへ渡さない。Core側で決定論的に閾値ラベル化し、情動は最大強度を
10段階バケットへ量子化、気分は上位1軸＋一次ダイアド最大1つを短い自然文にする。
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from mind.core.config import ThresholdsConfig
from mind.core.state.desire import (
    DesireState,
    is_desire_gate_open,
)
from mind.core.state.emotion import PLUTCHIK_AXES, EmotionState

NO_MOVEMENT_TEXT = "穏やかで、特に大きな波はない。"

# Task 3-5: 欲求が高いときの独立文（状態描写のみ。具体ワード例・解釈規則は書かない）
DESIRE_HIGH_LINE = "意識の端が、触れ合いや親密さの方に少し向きやすくなっている。"
# Task 3-5: 低〜中レベルで心情文に添える色（独立文にはしない）
DESIRE_TINT_PHRASE = "触れ合いや親密さの方へ、わずかに意識が傾いている。"

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

# 情動最大強度 0〜1 を10段階へ。各バケットに言い回し候補（バケット変更時のみ引き直し）。
AFFECT_BUCKET_VARIANTS: tuple[tuple[str, ...], ...] = (
    ("ほぼ平静",),
    ("かすかに揺れる", "わずかに揺れる"),
    ("静かに鼓動している", "静かに動いている"),
    ("少し高まっている", "少しだけ高まっている"),
    ("やや高ぶっている", "やや高まっている"),
    ("明確に動いている", "はっきり動いている"),
    ("感情が立ち上がっている", "心が立ち上がっている"),
    ("強く揺れ動いている", "激しく揺れ動いている"),
    ("限界近くまで張っている", "張り詰めに近い"),
    ("張り詰めている", "ぎりぎりまで張り詰めている"),
)

_MOOD_LABEL_TO_ADJECTIVE = {
    "軽い": "かすかな",
    "はっきりした": "はっきりした",
    "強い": "深い",
}


@dataclass
class EmotionRenderCache:
    """情動の言い回し据え置き用。バケットと軸の両方が同じときだけ再利用する。"""

    last_affect_bucket: int | None = None
    last_affect_axis: str | None = None
    last_affect_phrase: str | None = None


def _label(value: float, thresholds: ThresholdsConfig) -> str | None:
    if value < thresholds.emotion_ignore_below:
        return None
    if value < thresholds.emotion_mild_below:
        return "軽い"
    if value < thresholds.emotion_strong_below:
        return "はっきりした"
    return "強い"


def _max_affect(emotion: EmotionState) -> tuple[str, float] | None:
    best_axis = ""
    best_value = 0.0
    for axis in PLUTCHIK_AXES:
        value = emotion.affect.get(axis, 0.0)
        if value > best_value:
            best_axis = axis
            best_value = value
    if best_value <= 0.0:
        return None
    return best_axis, best_value


def _affect_bucket(value: float) -> int:
    if value >= 1.0:
        return 9
    return min(9, int(value * 10))


def _pick_affect_phrase(
    bucket: int,
    axis: str,
    cache: EmotionRenderCache,
) -> str:
    # 据え置きは「同じ強度帯の言い回しゆらぎ」用。軸が変わったら必ず更新する。
    if (
        cache.last_affect_bucket == bucket
        and cache.last_affect_axis == axis
        and cache.last_affect_phrase
    ):
        return cache.last_affect_phrase

    variants = AFFECT_BUCKET_VARIANTS[bucket]
    # 2026-07-26 A7: hash()はプロセス内で決定的（同じbucket/axisなら会話中つねに同じ言い回し
    # になり候補が死んでいた）。本物の乱数へ変更。バケット・軸が同じ間はキャッシュで
    # 据え置くため、ここは「新しく引き直すとき」だけ呼ばれる。
    base = random.choice(variants)
    if bucket == 0:
        phrase = base
    else:
        phrase = f"{base}（{axis}寄り）"
    cache.last_affect_bucket = bucket
    cache.last_affect_axis = axis
    cache.last_affect_phrase = phrase
    return phrase


def _top_mood_axis(
    values: dict[str, float], thresholds: ThresholdsConfig,
) -> tuple[str, str] | None:
    best: tuple[str, str, float] | None = None
    for axis in PLUTCHIK_AXES:
        value = values.get(axis, 0.0)
        label = _label(value, thresholds)
        if label is None:
            continue
        if best is None or value > best[2]:
            best = (axis, label, value)
    if best is None:
        return None
    return best[0], best[1]


def _best_dyad(
    values: dict[str, float], thresholds: ThresholdsConfig,
) -> str | None:
    """両軸がラベル可能かつ dyad_min 以上の一次ダイアドのうち、min強度が最大の名前を1つ返す。"""
    floor = thresholds.emotion_dyad_min
    best: tuple[str, float] | None = None
    for a, b, name in PRIMARY_DYADS:
        v1 = values.get(a, 0.0)
        v2 = values.get(b, 0.0)
        if _label(v1, thresholds) is None or _label(v2, thresholds) is None:
            continue
        strength = min(v1, v2)
        if strength < floor:
            continue
        if best is None or strength > best[1]:
            best = (name, strength)
    if best is None:
        return None
    return best[0]


def _render_mood_sentence(emotion: EmotionState, thresholds: ThresholdsConfig) -> str | None:
    top = _top_mood_axis(emotion.mood, thresholds)
    dyad = _best_dyad(emotion.mood, thresholds)
    if top is None and dyad is None:
        return None

    parts: list[str] = []
    if top is not None:
        axis, label = top
        adjective = _MOOD_LABEL_TO_ADJECTIVE.get(label, label)
        parts.append(f"底流には{adjective}{axis}が続いている。")
    if dyad is not None:
        parts.append(f"{dyad}も混じっている。")
    return "".join(parts)


def _get_cache(emotion: EmotionState, cache: EmotionRenderCache | None) -> EmotionRenderCache:
    if cache is not None:
        return cache
    existing = getattr(emotion, "render_cache", None)
    if existing is None:
        existing = EmotionRenderCache()
        emotion.render_cache = existing
    return existing


def render_emotion_for_pack(
    emotion: EmotionState,
    thresholds: ThresholdsConfig,
    *,
    cache: EmotionRenderCache | None = None,
    desire: DesireState | None = None,
) -> str:
    """情動・気分を自然文へ意訳する。生数値・注意書きは載せない。

    Task 3-5: 抑制門が開いているときだけ欲求の色を足す。
    level < 0.6 は心情文への色添え、level >= 0.6 は独立文。
    """
    render_cache = _get_cache(emotion, cache)
    lines: list[str] = []

    peak = _max_affect(emotion)
    if peak is not None:
        axis, value = peak
        if _label(value, thresholds) is not None:
            bucket = _affect_bucket(value)
            lines.append(_pick_affect_phrase(bucket, axis, render_cache))

    mood_line = _render_mood_sentence(emotion, thresholds)
    desire_high_line: str | None = None
    if desire is not None and is_desire_gate_open(
        emotion.affect,
        threshold=thresholds.desire_suppression_threshold,
    ):
        level_threshold = thresholds.desire_fulfillment_level_threshold
        if desire.level >= level_threshold:
            desire_high_line = DESIRE_HIGH_LINE
        elif desire.level > 0.0 and mood_line:
            # 低〜中: 心情の一文に色を添える（新しい独立文は作らない）
            mood_line = mood_line.rstrip("。") + "。" + DESIRE_TINT_PHRASE
        # 心情文が無いとき（desire.level > 0.0 and not mood_line）は色添え先が無いため、
        # 独立文にはせず何もしない（計画: 既存心情が空なら色も出さない）。

    if mood_line:
        lines.append(mood_line)
    if desire_high_line:
        lines.append(desire_high_line)

    if not lines:
        return NO_MOVEMENT_TEXT
    return "\n".join(lines)
