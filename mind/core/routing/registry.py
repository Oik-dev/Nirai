"""Brain登録簿の読み込み。設計書 §3.1

ルーティングは「表」であって「コード」ではない。追加・削除は登録簿に1行足す/消すだけ＋通訳を1枚書く。
手元（local）の脳は1つで、家の話・判定・眠りの主。クラウド（cloud）の脳は、登録簿の順に試す会話の脳。
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
    context_size: str
    model: str | None = None


def load_brain_registry(path: Path | None = None) -> list[BrainEntry]:
    target = path or DEFAULT_REGISTRY_PATH
    with target.open("rb") as f:
        raw = tomllib.load(f)

    return [
        BrainEntry(
            name=item["name"],
            adapter=item["adapter"],
            location=item["location"],
            context_size=item["context_size"],
            model=item.get("model"),
        )
        for item in raw.get("brain", [])
    ]


def local_brain(registry: list[BrainEntry]) -> BrainEntry:
    """手元の脳（家の話・判定・眠りの主。クラウドがどれも使えないときにも話す）。"""
    local = next((entry for entry in registry if entry.location == "local"), None)
    if local is None:
        raise ValueError("local brain is required")
    return local
