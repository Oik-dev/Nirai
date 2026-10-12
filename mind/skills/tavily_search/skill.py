"""Tavily 検索 Skill。裏方の道具（合言葉なし・状態や判断を持たない）。設計書 §5.6 改訂。

どの文を外へ出してよいかはCoreの家の印だけで決める。Skillは検索を実行する道具。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mind.skills.tavily_search.client import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_TAVILY_TIMEOUT_SECONDS,
    default_tavily_call,
    parse_tavily_response,
)


@dataclass(frozen=True)
class TavilyResult:
    """Tavily 検索の生の結果。title/snippet はここでは自然文のまま保持する
    （本文へ差し込むかどうかはCore/Voice側の責務。Skillは選別しない）。"""

    answer: str | None
    results: list[dict] = field(default_factory=list)


@dataclass
class TavilySearchSkill:
    """検索クエリのみをクラウドへ送る裏方の道具。"""

    api_key: str | None = None
    request_timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS
    max_results: int = DEFAULT_MAX_RESULTS
    call_fn: Callable[[str], dict] | None = None
    _last_failure_reason: str | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.call_fn)

    @property
    def last_failure_reason(self) -> str | None:
        """直近 search が None を返した理由（成功時は None）。関所の破棄記録・ログ用。"""
        return self._last_failure_reason

    def search(
        self,
        query: str,
    ) -> TavilyResult | None:
        """検索を実行する。Coreからは印が確認された文だけが届く。"""
        self._last_failure_reason = None
        if not self.enabled:
            self._last_failure_reason = "無効（APIキー・call_fn なし）"
            return None
        cleaned = query.strip()
        if not cleaned:
            self._last_failure_reason = "クエリが空"
            return None
        try:
            if self.call_fn is not None:
                data = self.call_fn(cleaned)
            else:
                assert self.api_key is not None
                data = default_tavily_call(
                    self.api_key,
                    cleaned,
                    timeout=self.request_timeout_seconds,
                    max_results=self.max_results,
                )
            answer, results = parse_tavily_response(data)
            return TavilyResult(answer=answer, results=results)
        except Exception as exc:  # noqa: BLE001
            self._last_failure_reason = f"{type(exc).__name__}: {exc}"
            return None


def load_tavily_search(
    *,
    api_key: str | None = None,
    request_timeout_seconds: float = DEFAULT_TAVILY_TIMEOUT_SECONDS,
    max_results: int = DEFAULT_MAX_RESULTS,
    call_fn: Callable[[str], dict] | None = None,
) -> TavilySearchSkill:
    """Skill を構築。API キーは Core 側ファクトリが `.env` の `TAVILY_API_KEY` から
    読んで渡す（Skill 自身は `.env` を読まない。A-5準拠、Gemini踏襲）。
    キー無しでも無効 Skill を返す（会話はGemmaで継続）。"""
    api_key = api_key or None
    return TavilySearchSkill(
        api_key=api_key,
        request_timeout_seconds=request_timeout_seconds,
        max_results=max_results,
        call_fn=call_fn,
    )
