"""Brain登録簿の読み込み。設計書 §3.1

ルーティングは「表」であって「コード」ではない。追加・削除は登録簿に1行足す/消すだけ＋通訳を1枚書く。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "brains.toml"


@dataclass(frozen=True)
class BrainEntry:
    name: str
    adapter: str
    location: str  # "cloud" | "local"
    role: str  # "primary" | "escalation" | "fallback"
    daily_quota: int  # -1 = 無制限
    per_minute_quota: int  # -1 = 無制限
    context_size: str
    supports_memory_tools: bool = False


def load_brain_registry(path: Path | None = None) -> list[BrainEntry]:
    target = path or DEFAULT_REGISTRY_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)

    return [
        BrainEntry(
            name=item["name"],
            adapter=item["adapter"],
            location=item["location"],
            role=item["role"],
            daily_quota=int(item["daily_quota"]),
            per_minute_quota=int(item["per_minute_quota"]),
            context_size=item["context_size"],
            supports_memory_tools=bool(item.get("supports_memory_tools", False)),
        )
        for item in raw.get("brain", [])
    ]
