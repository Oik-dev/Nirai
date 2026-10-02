"""Tavily 検索 API（検索特化・裏方の道具）。"""

from __future__ import annotations

import requests

TAVILY_ENDPOINT = "https://api.tavily.com/search"

# 無言単発検索用の短い専用タイムアウト（保留文がない構成で長い無音待ちは不可）。
DEFAULT_TAVILY_TIMEOUT_SECONDS = 8.0
DEFAULT_MAX_RESULTS = 5


class TavilyClientError(Exception):
    """Tavily API 応答の解釈失敗。"""


def default_tavily_call(
    api_key: str,
    query: str,
    *,
    timeout: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict:
    body = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "include_answer": True,
        "max_results": max_results,
    }
    response = requests.post(TAVILY_ENDPOINT, json=body, timeout=timeout)
    response.raise_for_status()
    return response.json()


def parse_tavily_response(data: dict) -> tuple[str | None, list[dict]]:
    """生 JSON から (answer, results[{title,url,snippet}]) を取り出す。

    title/snippet 等の自然文はここでは選別しない（Voiceへ渡すかどうかはCore/Voice側の責務）。
    本関数の役目は形の解釈のみ。
    """
    if not isinstance(data, dict):
        raise TavilyClientError(f"Tavily応答の形が想定外: {data!r}")
    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        answer = None
    else:
        answer = answer.strip()

    raw_results = data.get("results")
    results: list[dict] = []
    if isinstance(raw_results, list):
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            title = item.get("title")
            snippet = item.get("content") or item.get("snippet")
            results.append({
                "title": str(title or "").strip(),
                "url": url.strip(),
                "snippet": str(snippet or "").strip(),
            })
    return answer, results
