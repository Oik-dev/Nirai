"""External Services 共通契約（ServiceResult / ExternalConnector）

`基本設計書_ExternalServices接続.md` の型を実体化したもの。SearXNG 検索・将来の
Fetch / StockAI 等が共通で使う。将来 Perception 層の PerceptionResult はこれを拡張する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ServiceResult:
    status: str  # "ok" / "error"
    payload: str  # Aurora に注入する整形済み本文（上限は呼び出し側で管理）
    source: str  # 出所ラベル（例: "web_search"）。注入ブロックの見出しに使う
    error: str | None = None  # status="error" 時の理由（ログ用・ユーザーには見せない）


@runtime_checkable
class ExternalConnector(Protocol):
    def query(self, request: str) -> ServiceResult:
        ...
