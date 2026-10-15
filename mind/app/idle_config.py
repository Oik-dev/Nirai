"""アプリ層のタイマ設定の読み込み（config/app_timing.toml）。設計書 §2.4。

core/config.py(ThresholdsConfig=Coreの判断ツマミ)とは意図的に別ファイル・別クラスにする。
忙しさの決まり等は「アプリがいつ裏方に声をかけるか」の設定でありCoreの判断ではない（層を混ぜるとCoreにアプリ関心が漏れる）。
Serina 日の境目（朝7時）の正本は core/state/serina_day.py の SERINA_DAY_HOUR（記憶の「眠ったあとから思い出せる」と同じ時刻）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

from mind.core.chores.busy import BusyRule

DEFAULT_APP_TIMING_PATH = Path(__file__).resolve().parent.parent / "config" / "app_timing.toml"


@dataclass(frozen=True)
class AppTimingConfig:
    """config/app_timing.toml の値。数の正本は設定ファイルだけで、ここに既定値を持たない。"""

    idle_poll_interval_seconds: float
    serina_day_grace_after_activity_seconds: int
    sleep_retry_after_failure_seconds: int
    research_daily_limit: int
    research_follow_minutes: int
    research_agent: str
    research_agent_model: str
    research_agent_max_tokens: int
    busy: BusyRule


def load_app_timing(path: Path | None = None) -> AppTimingConfig:
    """設定ファイルを読む。キーが欠けていれば読まない（既定値へ黙って戻ると、名前の間違いに気づけない）。"""
    target = path or DEFAULT_APP_TIMING_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)
    try:
        watch, busy, serina_day, explore = raw["watch"], raw["busy"], raw["serina_day"], raw["explore"]
        poll = int(watch["poll_interval_seconds"])
        return AppTimingConfig(
            idle_poll_interval_seconds=poll,
            serina_day_grace_after_activity_seconds=int(serina_day["grace_after_activity_seconds"]),
            sleep_retry_after_failure_seconds=int(serina_day["sleep_retry_after_failure_seconds"]),
            research_daily_limit=min(6, max(0, int(explore["daily_limit"]))),  # 6が絶対上限（[explore]）
            research_follow_minutes=max(0, int(explore["continuation_minutes"])),
            research_agent=str(explore["agent"]),
            research_agent_model=str(explore["agent_model"]),
            research_agent_max_tokens=int(explore["agent_max_tokens"]),
            busy=BusyRule(
                every_seconds=poll,
                window_seconds=float(busy["window_seconds"]),
                quiet_after_seconds=float(busy["quiet_after_seconds"]),
                gpu_percent=float(busy["gpu_percent"]),
                cpu_percent=float(busy["cpu_percent"]),
            ),
        )
    except KeyError as e:
        raise ValueError(f"設定ファイルに {e} がない: {target}") from None
