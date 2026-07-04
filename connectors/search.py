"""WEB検索 Connector（SearXNG メタ検索・薄い）

通信・タイムアウト・整形だけ。判断ロジックは持たない（`OllamaChatConnector` と同格の薄さ）。
SearXNG は自前ホスト前提（既定 http://localhost:8888）。JSON 出力を有効化しておくこと
（settings.yml の search.formats に json を含める）。立て方は infra/searxng/ を参照。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from serina.connectors.external import ServiceResult

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str


class SearXNGConnector:
    def __init__(
        self,
        base_url: str = "http://localhost:8888",
        timeout: float = 30.0,
        top_n: int = 5,
        char_cap: int = 2000,
        language: str = "ja",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.top_n = top_n
        self.char_cap = char_cap
        self.language = language

    def query(self, request: str, *, news: bool = False) -> ServiceResult:
        """検索してスニペットを整形した ServiceResult を返す（注入用）。"""
        try:
            hits = self._search(request, news=news)
        except Exception as exc:  # 通信・HTTP・JSON をすべて error に畳む（無言死させない）
            logger.warning("SearXNG 検索に失敗: %s", exc)
            return ServiceResult(status="error", payload="", source="web_search", error=str(exc))
        if not hits:
            return ServiceResult(
                status="ok",
                payload="（該当する検索結果が見つかりませんでした）",
                source="web_search",
            )
        return ServiceResult(status="ok", payload=self._format(hits), source="web_search")

    def top_url(self, request: str, *, news: bool = False) -> str | None:
        """精読（Fetch, Phase 1b）用: 最上位ヒットの URL。失敗時は None。"""
        try:
            hits = self._search(request, news=news)
        except Exception as exc:
            logger.warning("SearXNG 検索(top_url)に失敗: %s", exc)
            return None
        return hits[0].url if hits else None

    def _search(self, request: str, *, news: bool) -> list[SearchHit]:
        params: dict[str, str] = {
            "q": request,
            "format": "json",
            "language": self.language,
        }
        if news:  # 「最新」系は news カテゴリ＋直近1ヶ月で、古い定番ページより新着を優先
            params["categories"] = "news"
            params["time_range"] = "month"
        resp = requests.get(f"{self.base_url}/search", params=params, timeout=self.timeout)
        resp.raise_for_status()
        results = resp.json().get("results") or []
        hits: list[SearchHit] = []
        for r in results[: self.top_n]:
            url = str(r.get("url", "")).strip()
            if not url:
                continue
            title = " ".join(str(r.get("title", "")).split()) or url
            snippet = " ".join(str(r.get("content", "")).split())
            hits.append(SearchHit(title=title, url=url, snippet=snippet))
        return hits

    def _format(self, hits: list[SearchHit]) -> str:
        lines: list[str] = []
        used = 0
        for h in hits:
            block = f"- {h.title}\n  {h.snippet}\n  {h.url}" if h.snippet else f"- {h.title}\n  {h.url}"
            if used + len(block) + 1 > self.char_cap:
                break
            lines.append(block)
            used += len(block) + 1
        return "\n".join(lines)
