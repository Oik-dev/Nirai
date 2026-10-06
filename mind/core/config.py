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
    persona_propose_reflection_limit: int = 3
    persona_propose_max_retries: int = 3
    recent_turns_small: int = 24
    recent_turns_large: int = 64
    fine_band_turns: int = 20
    coarse_update_every_n_turns: int = 10
    ollama_request_timeout_seconds: float = 240.0
    ollama_num_ctx: int = 8192
    ollama_use_mmap: bool = True
    embedder_request_timeout_seconds: float = 30.0
    # 外聞き（Gemini アドバイザー）: 1ターンの相談合計時間予算（§5.6）
    advisor_turn_budget_seconds: float = 180.0
    # Pulse（§2.8）
    pulse_active_hour_start: int = 8
    pulse_active_hour_end: int = 22
    pulse_same_kind_gap_seconds: float = 10800.0
    pulse_min_interval_seconds: float = 3600.0
    pulse_late_night_start: int = 23
    pulse_late_night_end: int = 7
    persona_blade_visible_brake_mode: str = "parenthetical"
    # 気持ち（§2.3）。正本は設定ファイルの [feeling] だけ（既定値をここに複製しない）。テストで気持ちを使わないときは None
    feeling: FeelingParams | None = None

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
    target = path or DEFAULT_THRESHOLDS_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)

    chores = raw.get("chores", {})
    context = raw.get("context", {})
    ollama = raw.get("ollama", {})
    embedder = raw.get("embedder", {})
    advisor = raw.get("advisor", {})
    pulse = raw.get("pulse", {})
    persona_blade = raw.get("persona_blade", {})
    if "feeling" not in raw:
        raise ValueError(f"[feeling] が設定ファイルに存在しない: {target}")

    return ThresholdsConfig(
        persona_propose_reflection_limit=int(chores.get("persona_propose_reflection_limit", 3)),
        persona_propose_max_retries=int(chores.get("persona_propose_max_retries", 3)),
        recent_turns_small=int(context.get("recent_turns_small", 24)),
        recent_turns_large=int(context.get("recent_turns_large", 64)),
        fine_band_turns=int(context.get("fine_band_turns", 20)),
        coarse_update_every_n_turns=int(context.get("coarse_update_every_n_turns", 10)),
        ollama_request_timeout_seconds=float(ollama.get("request_timeout_seconds", 240)),
        ollama_num_ctx=int(ollama.get("num_ctx", 8192)),
        ollama_use_mmap=bool(ollama.get("use_mmap", True)),
        embedder_request_timeout_seconds=float(embedder.get("request_timeout_seconds", 30)),
        advisor_turn_budget_seconds=float(advisor.get("turn_budget_seconds", 180)),
        pulse_active_hour_start=int(pulse.get("active_hour_start", 8)),
        pulse_active_hour_end=int(pulse.get("active_hour_end", 22)),
        pulse_same_kind_gap_seconds=float(pulse.get("same_kind_gap_seconds", 10800)),
        pulse_min_interval_seconds=float(pulse.get("min_interval_seconds", 3600)),
        pulse_late_night_start=int(pulse.get("late_night_start", 23)),
        pulse_late_night_end=int(pulse.get("late_night_end", 7)),
        persona_blade_visible_brake_mode=str(
            persona_blade.get("visible_brake_mode", "parenthetical"),
        ),
        feeling=load_feeling_params(raw["feeling"], target),
    )
