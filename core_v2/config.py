"""数値ツマミの読み込み。設計書v2 §5.5-3: 閾値・幅はすべて設定ファイル化しハードコード禁止。"""

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
    memory_max_candidates_per_session: int = 5
    aurora_extraction_max_retries: int = 3
    chore_fragment_turns: int = 20
    recent_turns_small: int = 24
    recent_turns_large: int = 64
    aurora_request_timeout_seconds: float = 180.0
    gemini_request_timeout_seconds: float = 30.0
    embedder_request_timeout_seconds: float = 30.0

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
    gemini = raw.get("gemini", {})
    embedder = raw.get("embedder", {})

    return ThresholdsConfig(
        fusen_confidence=fusen_confidence,
        mood_guard_max_delta_per_turn=float(max_delta),
        memory_dedup_threshold=float(memory.get("dedup_threshold", 0.92)),
        memory_max_candidates_per_session=int(memory.get("max_candidates_per_session", 5)),
        aurora_extraction_max_retries=int(aurora.get("extraction_max_retries", 3)),
        chore_fragment_turns=int(chores.get("fragment_turns", 20)),
        recent_turns_small=int(context.get("recent_turns_small", 24)),
        recent_turns_large=int(context.get("recent_turns_large", 64)),
        aurora_request_timeout_seconds=float(aurora.get("request_timeout_seconds", 180)),
        gemini_request_timeout_seconds=float(gemini.get("request_timeout_seconds", 30)),
        embedder_request_timeout_seconds=float(embedder.get("request_timeout_seconds", 30)),
    )
