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
    fact_supersede_similarity_threshold: float = 0.85
    hypothesis_retention_days: int = 30
    memory_max_candidates_per_job: int = 3
    memory_min_quote_length: int = 8
    persona_propose_diary_limit: int = 3
    persona_propose_max_retries: int = 3
    chore_fragment_turns: int = 10
    recent_turns_small: int = 24
    recent_turns_large: int = 64
    fine_band_turns: int = 20
    coarse_update_every_n_turns: int = 10
    ollama_request_timeout_seconds: float = 240.0
    ollama_num_ctx: int = 8192
    embedder_request_timeout_seconds: float = 30.0
    # 外聞き（Gemini アドバイザー）: 1ターンの相談合計時間予算（§5.6）
    advisor_turn_budget_seconds: float = 180.0
    emotion_ignore_below: float = 0.15
    emotion_mild_below: float = 0.4
    emotion_strong_below: float = 0.7
    emotion_affect_top_n: int = 2
    tau_affect_seconds: float = 7200.0
    tau_mood_seconds: float = 259200.0
    tau_baseline_seconds: float = 1_209_600.0
    emotion_dyad_min: float = 0.4
    emotion_baselines: dict[str, float] | None = None
    emotion_baseline_max: float = 0.5
    # 欲求層（Phase 3）
    desire_suppression_threshold: float = 0.6
    desire_fulfillment_level_threshold: float = 0.6
    desire_fulfillment_delta_threshold: float = 0.3
    desire_fulfillment_boost: float = 0.3
    desire_refractory_seconds: float = 129_600.0
    desire_decay_tau_seconds: float = 2_592_000.0  # 30日（2026-07-30 レビューC-2是正）
    desire_discharge_level: float = 0.05
    # 2026-07-26 A5: 対極カップリング比率（§2.3）。正の明示デルタの対極軸へ引く強さ。
    emotion_opposite_coupling_ratio: float = 0.5
    # 2026-07-26 A6: 気分が情動へにじむ速さ（§2.3）。mood_guard_max_delta_per_turnは
    # にじみ量の安全上限として流用する（上限であり主機構ではない）。
    emotion_mood_bleed_rate: float = 0.08
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
    # Pulse（§3.6）
    pulse_idle_before_seconds: float = 2700.0
    pulse_active_hour_start: int = 8
    pulse_active_hour_end: int = 22
    pulse_same_kind_gap_seconds: float = 10800.0
    pulse_emotion_gap_seconds: float = 21600.0
    pulse_min_interval_seconds: float = 3600.0
    pulse_late_night_start: int = 23
    pulse_late_night_end: int = 7
    pulse_mood_deviation_threshold: float = 0.55
    persona_blade_visible_brake_mode: str = "parenthetical"
    # 2026-07-26 B1: マスター観測（関係状態）の鮮度切れ判定（§1.5⑤・§2.6）。
    # これより古い観測はパックへ出さない（時刻の錨が無いまま「今の様子」として
    # 提示すると誤読を招くため。architecture-reviewer指摘）。
    relationship_observation_stale_after_seconds: float = 21600.0

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

    def pulse_config(self):  # noqa: ANN201
        """idle_policy.PulseConfig へ変換。"""
        from mind.core.chores.idle_policy import PulseConfig

        return PulseConfig(
            idle_before_seconds=self.pulse_idle_before_seconds,
            active_hour_start=self.pulse_active_hour_start,
            active_hour_end=self.pulse_active_hour_end,
            same_kind_gap_seconds=self.pulse_same_kind_gap_seconds,
            emotion_gap_seconds=self.pulse_emotion_gap_seconds,
            min_interval_seconds=self.pulse_min_interval_seconds,
            late_night_start=self.pulse_late_night_start,
            late_night_end=self.pulse_late_night_end,
            mood_deviation_threshold=self.pulse_mood_deviation_threshold,
        )


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
    chores = raw.get("chores", {})
    context = raw.get("context", {})
    ollama = raw.get("ollama", {})
    embedder = raw.get("embedder", {})
    advisor = raw.get("advisor", {})
    emotion_render = raw.get("emotion_render", {})
    emotion_decay = raw.get("emotion_decay", {})
    emotion_baseline = raw.get("emotion_baseline", {})
    recall = raw.get("recall", {})
    pulse = raw.get("pulse", {})
    persona_blade = raw.get("persona_blade", {})
    relationship = raw.get("relationship", {})
    desire = raw.get("desire", {})

    baselines = {
        str(k): float(v) for k, v in emotion_baseline.items() if str(k) != "max"
    }
    baseline_max = float(emotion_baseline.get("max", 0.5))

    return ThresholdsConfig(
        fusen_confidence=fusen_confidence,
        mood_guard_max_delta_per_turn=float(max_delta),
        memory_dedup_threshold=float(memory.get("dedup_threshold", 0.92)),
        fact_supersede_similarity_threshold=float(
            memory.get("fact_supersede_similarity_threshold", 0.85),
        ),
        hypothesis_retention_days=int(memory.get("hypothesis_retention_days", 30)),
        memory_max_candidates_per_job=int(memory.get("max_candidates_per_job", 3)),
        memory_min_quote_length=int(memory.get("min_quote_length", 8)),
        persona_propose_diary_limit=int(chores.get("persona_propose_diary_limit", 3)),
        persona_propose_max_retries=int(chores.get("persona_propose_max_retries", 3)),
        chore_fragment_turns=int(chores.get("fragment_turns", 10)),
        recent_turns_small=int(context.get("recent_turns_small", 24)),
        recent_turns_large=int(context.get("recent_turns_large", 64)),
        fine_band_turns=int(context.get("fine_band_turns", 20)),
        coarse_update_every_n_turns=int(context.get("coarse_update_every_n_turns", 10)),
        ollama_request_timeout_seconds=float(ollama.get("request_timeout_seconds", 240)),
        ollama_num_ctx=int(ollama.get("num_ctx", 8192)),
        embedder_request_timeout_seconds=float(embedder.get("request_timeout_seconds", 30)),
        advisor_turn_budget_seconds=float(advisor.get("turn_budget_seconds", 180)),
        emotion_ignore_below=float(emotion_render.get("ignore_below", 0.15)),
        emotion_mild_below=float(emotion_render.get("mild_below", 0.4)),
        emotion_strong_below=float(emotion_render.get("strong_below", 0.7)),
        emotion_affect_top_n=int(emotion_render.get("affect_top_n", 2)),
        tau_affect_seconds=float(emotion_decay.get("tau_affect_seconds", 7200)),
        tau_mood_seconds=float(emotion_decay.get("tau_mood_seconds", 259200)),
        tau_baseline_seconds=float(emotion_decay.get("tau_baseline_seconds", 1_209_600)),
        emotion_dyad_min=float(emotion_decay.get("dyad_min", 0.4)),
        emotion_opposite_coupling_ratio=float(emotion_decay.get("opposite_coupling_ratio", 0.5)),
        emotion_mood_bleed_rate=float(emotion_decay.get("mood_bleed_rate", 0.08)),
        emotion_baselines=baselines,
        emotion_baseline_max=baseline_max,
        desire_suppression_threshold=float(desire.get("suppression_threshold", 0.6)),
        desire_fulfillment_level_threshold=float(
            desire.get("fulfillment_level_threshold", 0.6),
        ),
        desire_fulfillment_delta_threshold=float(
            desire.get("fulfillment_delta_threshold", 0.3),
        ),
        desire_fulfillment_boost=float(desire.get("fulfillment_boost", 0.3)),
        desire_refractory_seconds=float(desire.get("refractory_seconds", 129_600)),
        desire_decay_tau_seconds=float(desire.get("decay_tau_seconds", 2_592_000)),
        desire_discharge_level=float(desire.get("discharge_level", 0.05)),
        recall_weight_relevance=float(recall.get("weight_relevance", 0.6)),
        recall_weight_importance=float(recall.get("weight_importance", 0.15)),
        recall_weight_recency=float(recall.get("weight_recency", 0.05)),
        recall_grade_bonus_s=float(recall.get("grade_bonus_s", 0.20)),
        recall_grade_bonus_a=float(recall.get("grade_bonus_a", 0.15)),
        recall_spread_decay=float(recall.get("spread_decay", 0.5)),
        recall_spread_seeds=int(recall.get("spread_seeds", 3)),
        recall_noise_sigma=float(recall.get("noise_sigma", 0.02)),
        recall_activation_floor=float(recall.get("activation_floor", 0.5)),
        pulse_idle_before_seconds=float(pulse.get("idle_before_seconds", 2700)),
        pulse_active_hour_start=int(pulse.get("active_hour_start", 8)),
        pulse_active_hour_end=int(pulse.get("active_hour_end", 22)),
        pulse_same_kind_gap_seconds=float(pulse.get("same_kind_gap_seconds", 10800)),
        pulse_emotion_gap_seconds=float(pulse.get("emotion_gap_seconds", 21600)),
        pulse_min_interval_seconds=float(pulse.get("min_interval_seconds", 3600)),
        pulse_late_night_start=int(pulse.get("late_night_start", 23)),
        pulse_late_night_end=int(pulse.get("late_night_end", 7)),
        pulse_mood_deviation_threshold=float(pulse.get("mood_deviation_threshold", 0.55)),
        persona_blade_visible_brake_mode=str(
            persona_blade.get("visible_brake_mode", "parenthetical"),
        ),
        relationship_observation_stale_after_seconds=float(
            relationship.get("observation_stale_after_seconds", 21600),
        ),
    )
