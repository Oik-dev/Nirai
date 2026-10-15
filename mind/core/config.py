"""数値ツマミの読み込み。設計書 §5.5-3: 閾値・幅はすべて設定ファイル化しハードコード禁止。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from mind.core.feeling.appraisal import AROUSAL, DISTANCE, VALENCE
from mind.core.feeling.body import FeelingParams

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parent.parent / "config" / "thresholds.toml"


@dataclass(frozen=True)
class ThresholdsConfig:
    """設定ファイル（config/thresholds.toml）のツマミ。数の正本は設定ファイルだけで、ここに既定値を持たない。"""

    persona_propose_max_retries: int
    recent_turns_small: int
    recent_turns_large: int
    fine_band_turns: int
    coarse_update_every_n_turns: int
    ollama_request_timeout_seconds: float
    ollama_num_ctx: int
    ollama_use_mmap: bool
    lookup_max_lines: int
    lookup_neighbors: int
    lookup_small_chars: int
    lookup_large_chars: int
    lookup_total_small_chars: int
    lookup_total_large_chars: int
    embedder_request_timeout_seconds: float
    # Pulse（§2.8）
    pulse_active_hour_start: int
    pulse_active_hour_end: int
    pulse_same_kind_gap_seconds: float
    pulse_min_interval_seconds: float
    pulse_late_night_start: int
    pulse_late_night_end: int
    persona_blade_visible_brake_mode: str
    # 気持ち（§2.3）
    feeling: FeelingParams

    def recent_turns_for(self, context_size: str | None) -> int:
        """§1.4: Brainのcontext_sizeに応じた直近ターン窓の幅を返す。"""
        if context_size == "large":
            return self.recent_turns_large
        return self.recent_turns_small

    def pulse_config(self):  # noqa: ANN201
        """idle_policy.PulseConfig へ変換。"""
        from mind.core.chores.idle_policy import PulseConfig

        return PulseConfig(
            active_hour_start=self.pulse_active_hour_start,
            active_hour_end=self.pulse_active_hour_end,
            same_kind_gap_seconds=self.pulse_same_kind_gap_seconds,
            min_interval_seconds=self.pulse_min_interval_seconds,
            late_night_start=self.pulse_late_night_start,
            late_night_end=self.pulse_late_night_end,
        )


def _choices(table: dict, choices: tuple[str, ...], name: str, target: Path) -> dict[str, float]:
    """評価の選択肢ごとの動き。選択肢と同じ顔ぶれでなければ読まない（対応表の抜けは、気持ちが黙って動かなくなるため）。"""
    if set(table) != set(choices):
        raise ValueError(f"[feeling.{name}] の選択肢が {choices} と合わない: {sorted(table)}（{target}）")
    return {choice: float(table[choice]) for choice in choices}


def load_feeling_params(raw: dict, target: Path) -> FeelingParams:
    return FeelingParams(
        tau_fast_seconds=float(raw["tau_fast_seconds"]),
        tau_slow_seconds=float(raw["tau_slow_seconds"]),
        slow_share=float(raw["slow_share"]),
        tau_connection_seconds=float(raw["tau_connection_seconds"]),
        initial_connection=float(raw["initial_connection"]),
        temperament_valence=float(raw["temperament_valence"]),
        loneliness_valence=float(raw["loneliness_valence"]),
        arousal_mid=float(raw["arousal_mid"]),
        arousal_swing=float(raw["arousal_swing"]),
        arousal_peak_hour=float(raw["arousal_peak_hour"]),
        meeting_valence=float(raw["meeting_valence"]),
        valence=_choices(raw["valence"], VALENCE, "valence", target),
        arousal=_choices(raw["arousal"], AROUSAL, "arousal", target),
        distance=_choices(raw["distance"], DISTANCE, "distance", target),
        bright_above=float(raw["bright_above"]),
        dim_below=float(raw["dim_below"]),
        stirred_above=float(raw["stirred_above"]),
        calm_below=float(raw["calm_below"]),
        sleepy_below=float(raw["sleepy_below"]),
        lonely_below=float(raw["lonely_below"]),
        flow_items=int(raw["flow_items"]),
        master_state_stale_after_seconds=float(raw["master_state_stale_after_seconds"]),
    )


def load_thresholds(path: Path | None = None) -> ThresholdsConfig:
    """設定ファイルを読む。キーが欠けていれば読まない（既定値へ黙って戻ると、名前の間違いに気づけない）。"""
    target = path or DEFAULT_THRESHOLDS_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)
    try:
        chores, context, lookup = raw["chores"], raw["context"], raw["lookup"]
        ollama, embedder, pulse = raw["ollama"], raw["embedder"], raw["pulse"]
        return ThresholdsConfig(
            persona_propose_max_retries=int(chores["persona_propose_max_retries"]),
            recent_turns_small=int(context["recent_turns_small"]),
            recent_turns_large=int(context["recent_turns_large"]),
            fine_band_turns=int(context["fine_band_turns"]),
            coarse_update_every_n_turns=int(context["coarse_update_every_n_turns"]),
            ollama_request_timeout_seconds=float(ollama["request_timeout_seconds"]),
            ollama_num_ctx=int(ollama["num_ctx"]),
            ollama_use_mmap=bool(ollama["use_mmap"]),
            lookup_max_lines=int(lookup["max_lines"]),
            lookup_neighbors=int(lookup["neighbors"]),
            lookup_small_chars=int(lookup["small_chars"]),
            lookup_large_chars=int(lookup["large_chars"]),
            lookup_total_small_chars=int(lookup["total_small_chars"]),
            lookup_total_large_chars=int(lookup["total_large_chars"]),
            embedder_request_timeout_seconds=float(embedder["request_timeout_seconds"]),
            pulse_active_hour_start=int(pulse["active_hour_start"]),
            pulse_active_hour_end=int(pulse["active_hour_end"]),
            pulse_same_kind_gap_seconds=float(pulse["same_kind_gap_seconds"]),
            pulse_min_interval_seconds=float(pulse["min_interval_seconds"]),
            pulse_late_night_start=int(pulse["late_night_start"]),
            pulse_late_night_end=int(pulse["late_night_end"]),
            persona_blade_visible_brake_mode=str(raw["persona_blade"]["visible_brake_mode"]),
            feeling=load_feeling_params(raw["feeling"], target),
        )
    except KeyError as e:
        raise ValueError(f"設定ファイルに {e} がない: {target}") from None
