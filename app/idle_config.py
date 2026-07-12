"""アプリ層のタイマ設定の読み込み。設計書v2 §2.4。

core_v2/config.py(ThresholdsConfig=Coreの判断ツマミ)とは意図的に別ファイル・別クラスにする。
GPU閾値等は「アプリがいつ裏方便に声をかけるか」の設定でありCoreの判断ではない
（advisorレビュー2026-07-11: 層を混ぜるとCoreにアプリ関心が漏れる）。

心拍関連の設定は2026-07-12に廃止した（idle_policy.py参照）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP_TIMING_PATH = Path(__file__).resolve().parent.parent / "config" / "app_timing.toml"


@dataclass(frozen=True)
class AppTimingConfig:
    idle_timeout_after_seconds: int = 300
    idle_digest_gap_seconds: int = 60
    idle_poll_interval_seconds: int = 20
    idle_digest_chunk_limit: int = 1
    gpu_busy_threshold_percent: float = 40.0
    diary_min_gap_seconds: int = 21600


def load_app_timing(path: Path | None = None) -> AppTimingConfig:
    target = path or DEFAULT_APP_TIMING_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)

    idle = raw.get("idle", {})
    gpu = raw.get("gpu", {})

    return AppTimingConfig(
        idle_timeout_after_seconds=int(idle.get("timeout_after_seconds", 300)),
        idle_digest_gap_seconds=int(idle.get("digest_gap_seconds", 60)),
        idle_poll_interval_seconds=int(idle.get("poll_interval_seconds", 20)),
        idle_digest_chunk_limit=int(idle.get("digest_chunk_limit", 1)),
        gpu_busy_threshold_percent=float(gpu.get("busy_threshold_percent", 40.0)),
        diary_min_gap_seconds=int(raw.get("diary", {}).get("min_gap_seconds", 21600)),
    )
