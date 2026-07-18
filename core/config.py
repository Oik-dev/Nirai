"""数値ツマミの読み込み。設計書 §5.5-3: 閾値・幅はすべて設定ファイル化しハードコード禁止。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.toml"


@dataclass(frozen=True)
class ThresholdsConfig:
    fusen_confidence: dict[str, float]
    mood_guard_max_delta_per_turn: float
    memory_dedup_threshold: float = 0.92
    memory_max_candidates_per_job: int = 5
    memory_min_quote_length: int = 8
    aurora_extraction_max_retries: int = 3
    aurora_assessment_max_retries: int = 3
    chore_fragment_turns: int = 20
    recent_turns_small: int = 24
    recent_turns_large: int = 64
    aurora_request_timeout_seconds: float = 180.0
    qwen_request_timeout_seconds: float = 240.0
    embedder_request_timeout_seconds: float = 30.0
    emotion_ignore_below: float = 0.15
    emotion_mild_below: float = 0.4
    emotion_strong_below: float = 0.7
    emotion_affect_top_n: int = 2
    # 想起の活性化モデル（§4.4 2026-07-17改訂）
    recall_weight_relevance: float = 0.6
    recall_weight_importance: float = 0.15
    recall_weight_recency: float = 0.05
    recall_grade_bonus_s: float = 0.20
    recall_grade_bonus_a: float = 0.15
    recall_spread_decay: float = 0.5
    recall_spread_seeds: int = 3
    recall_noise_sigma: float = 0.02
    recall_activation_floor: float = 0.5

    @property
    def default_confidence_threshold(self) -> float:
        return self.fusen_confidence.get("default", 0.5)

    def confidence_threshold_for(self, kind: str) -> float:
        return self.fusen_confidence.get(kind, self.default_confidence_threshold)

    def recent_turns_for(self, context_size: str | None) -> int:
        """§1.4: Brainのcontext_sizeに応じた直近ターン窓の幅を返す。"""
        if context_size == "large":
            return self.recent_turns_large
        return self.recent_turns_small


def load_thresholds(path: Path | None = None) -> ThresholdsConfig:
    target = path or DEFAULT_THRESHOLDS_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)

    fusen_confidence = raw.get("fusen_confidence", {})
    mood_guard = raw.get("mood_guard", {})
    max_delta = mood_guard.get("max_delta_per_turn")
    if max_delta is None:
        raise ValueError(f"mood_guard.max_delta_per_turn が設定ファイルに存在しない: {target}")

    memory = raw.get("memory", {})
    aurora = raw.get("aurora", {})
    chores = raw.get("chores", {})
    context = raw.get("context", {})
    qwen = raw.get("qwen", {})
    embedder = raw.get("embedder", {})
    emotion_render = raw.get("emotion_render", {})
    recall = raw.get("recall", {})

    return ThresholdsConfig(
        fusen_confidence=fusen_confidence,
        mood_guard_max_delta_per_turn=float(max_delta),
        memory_dedup_threshold=float(memory.get("dedup_threshold", 0.92)),
        memory_max_candidates_per_job=int(memory.get("max_candidates_per_job", 5)),
        memory_min_quote_length=int(memory.get("min_quote_length", 8)),
        aurora_extraction_max_retries=int(aurora.get("extraction_max_retries", 3)),
        aurora_assessment_max_retries=int(aurora.get("assessment_max_retries", 3)),
        chore_fragment_turns=int(chores.get("fragment_turns", 20)),
        recent_turns_small=int(context.get("recent_turns_small", 24)),
        recent_turns_large=int(context.get("recent_turns_large", 64)),
        aurora_request_timeout_seconds=float(aurora.get("request_timeout_seconds", 180)),
        qwen_request_timeout_seconds=float(qwen.get("request_timeout_seconds", 240)),
        embedder_request_timeout_seconds=float(embedder.get("request_timeout_seconds", 30)),
        emotion_ignore_below=float(emotion_render.get("ignore_below", 0.15)),
        emotion_mild_below=float(emotion_render.get("mild_below", 0.4)),
        emotion_strong_below=float(emotion_render.get("strong_below", 0.7)),
        emotion_affect_top_n=int(emotion_render.get("affect_top_n", 2)),
        recall_weight_relevance=float(recall.get("weight_relevance", 0.6)),
        recall_weight_importance=float(recall.get("weight_importance", 0.15)),
        recall_weight_recency=float(recall.get("weight_recency", 0.05)),
        recall_grade_bonus_s=float(recall.get("grade_bonus_s", 0.20)),
        recall_grade_bonus_a=float(recall.get("grade_bonus_a", 0.15)),
        recall_spread_decay=float(recall.get("spread_decay", 0.5)),
        recall_spread_seeds=int(recall.get("spread_seeds", 3)),
        recall_noise_sigma=float(recall.get("noise_sigma", 0.02)),
        recall_activation_floor=float(recall.get("activation_floor", 0.5)),
    )
