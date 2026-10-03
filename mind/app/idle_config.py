"""アプリ層のタイマ設定の読み込み（config/app_timing.toml）。設計書 §2.4。

core/config.py(ThresholdsConfig=Coreの判断ツマミ)とは意図的に別ファイル・別クラスにする。
GPU閾値等は「アプリがいつ裏方に声をかけるか」の設定でありCoreの判断ではない（層を混ぜるとCoreにアプリ関心が漏れる）。
Serina 日の境目（朝7時）の正本は core/state/serina_day.py の SERINA_DAY_HOUR（記憶の「眠ったあとから思い出せる」と同じ時刻）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_APP_TIMING_PATH = Path(__file__).resolve().parent.parent / "config" / "app_timing.toml"


@dataclass(frozen=True)
class AppTimingConfig:
    idle_poll_interval_seconds: int = 20
    gpu_busy_threshold_percent: float = 25.0
    serina_day_grace_after_activity_seconds: int = 900
    sleep_retry_after_failure_seconds: int = 600


def load_app_timing(path: Path | None = None) -> AppTimingConfig:
    target = path or DEFAULT_APP_TIMING_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)
    watch = raw.get("watch", {})
    gpu = raw.get("gpu", {})
    serina_day = raw.get("serina_day", {})
    return AppTimingConfig(
        idle_poll_interval_seconds=int(watch.get("poll_interval_seconds", 20)),
        gpu_busy_threshold_percent=float(gpu.get("busy_threshold_percent", 25.0)),
        serina_day_grace_after_activity_seconds=int(serina_day.get("grace_after_activity_seconds", 900)),
        sleep_retry_after_failure_seconds=int(serina_day.get("sleep_retry_after_failure_seconds", 600)),
    )
