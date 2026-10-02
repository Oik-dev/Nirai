"""Tavily 検索 Skill。裏方の道具（合言葉なし・状態や判断を持たない）。設計書 §5.6 改訂。

会話 Brain ではない（Gemini と同様、primary/escalation には登録しない）。
機微の関所の主体は Core（`core/intake/advisor_tools.execute_tavily_search`）に置く。
ここに残す関所は Core の判断漏れに備えた従属的な最終防御網であり、Skill 自身が
独立に「送るか送らないか」を決める第二の決定主体にはしない（A-5準拠。
実装前レビュー指摘で確定した設計。詳細は execute_tavily_search の docstring を参照）。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from mind.skills.tavily_search.client import (
    DEFAULT_MAX_RESULTS,
    DEFAULT_TAVILY_TIMEOUT_SECONDS,
    default_tavily_call,
    parse_tavily_response,
)


class SensitivityRules(Protocol):
    """機微判定の受け口（`gemini_advisor.payload.SensitivityRules` と同型・独立定義。
    Skill から Core を参照し返さない設計のため、あえて共有しない）。"""

    def is_sensitive(self, text: str) -> bool: ...


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
        *,
        routing_rules: SensitivityRules | None = None,
    ) -> TavilyResult | None:
        """検索を実行する。失敗・無効・機微拒否時は None（例外を握って会話継続を優先）。

        従属的な最終防御網（fail-closed）: `routing_rules` が None、または `query` が
        機微なら送信しない。主たる関所は呼び出し元（Core の `execute_tavily_search`）が
        Skill 呼び出し前に確定させる想定だが、本メソッド単体で呼ばれても安全側であることを
        保証する。本メソッドのシグネチャには `master_utterance`（原発話）を一切含めない
        （機微を含みうる生の原発話を道具の手元まで渡さない設計）。
        """
        self._last_failure_reason = None
        if not self.enabled:
            self._last_failure_reason = "無効（APIキー・call_fn なし）"
            return None
        cleaned = query.strip()
        if not cleaned:
            self._last_failure_reason = "クエリが空"
            return None
        if routing_rules is None:
            self._last_failure_reason = "門番（routing_rules）未接続のため送信拒否"
            return None
        if routing_rules.is_sensitive(cleaned):
            self._last_failure_reason = "機微フィルタで拒否（送信せず）"
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
    キー無しでも無効 Skill を返す（会話は継続）。"""
    api_key = api_key or None
    return TavilySearchSkill(
        api_key=api_key,
        request_timeout_seconds=request_timeout_seconds,
        max_results=max_results,
        call_fn=call_fn,
    )
