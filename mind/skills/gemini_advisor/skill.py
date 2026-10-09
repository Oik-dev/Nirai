"""Gemini / Antigravity 無人格アドバイザー Skill。会話 Brain ではない（設計書 §5.6）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from mind.skills.gemini_advisor.client import (
    ANTIGRAVITY_AGENT,
    ANTIGRAVITY_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_SECONDS,
    GOOGLE_SEARCH_TOOL,
    antigravity_tools_for_category,
    default_antigravity_call,
    model_for_category,
    wants_google_search,
)
from mind.skills.gemini_advisor.instructions import system_instruction_for
from mind.skills.gemini_advisor.payload import SensitivityRules, build_payload


@dataclass
class GeminiAdvisorSkill:
    """相談クエリのみをクラウドへ送る無人格アドバイザー。

    当面（この API キーの Flash Search 不通期間）:
    - web_search / general → Antigravity（google_search + url_context）
    - code_qa → Antigravity（code_execution）
    Flash 連鎖は client.WEB_MODEL_FALLBACK_CHAIN に残置（枠復活時に再配線）。
    """

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    antigravity_timeout_seconds: float = ANTIGRAVITY_TIMEOUT_SECONDS
    call_fn: Callable[[dict], str] | None = None
    _last_request_body: dict = field(default_factory=dict, repr=False)
    _last_model: str | None = field(default=None, repr=False)
    _last_failure_reason: str | None = field(default=None, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.call_fn)

    @property
    def last_request_body(self) -> dict:
        """直近 consult の API body（テスト・監査用）。"""
        return dict(self._last_request_body)

    @property
    def last_model(self) -> str | None:
        """直近 consult で実際に成功したモデル／エージェント ID。"""
        return self._last_model

    @property
    def last_failure_reason(self) -> str | None:
        """直近 consult が None を返した理由（成功時は None）。関所の破棄記録・ログ用。"""
        return self._last_failure_reason

    def consult(
        self,
        query: str,
        *,
        category: str = "general",
        routing_rules: SensitivityRules | None = None,
    ) -> str | None:
        """相談クエリを送り素の回答を返す。失敗・無効時は None（会話は継続）。

        routing_rules 未接続（None）は既定拒否。門番を通さない直呼びで
        クエリが素通しになる経路を構造的に塞ぐ（§5.6）。
        """
        self._last_failure_reason = None
        if not self.enabled:
            self._last_failure_reason = "無効（APIキー・call_fn なし）"
            return None
        if routing_rules is None:
            self._last_failure_reason = "門番（routing_rules）未接続のため送信拒否"
            return None
        if not query.strip():
            self._last_failure_reason = "クエリが空"
            return None
        payload = build_payload(query, category=category, routing_rules=routing_rules)
        if payload is None:
            self._last_failure_reason = "機微フィルタで拒否（送信せず）"
            return None
        self._last_model = model_for_category(payload.category)
        try:
            if self.call_fn is not None:
                # テスト注入用: generateContent 形の body を渡す
                system = system_instruction_for(payload.category)
                body = payload.to_api_body(system_instruction=system)
                if wants_google_search(payload.category):
                    body = {**body, "tools": [GOOGLE_SEARCH_TOOL]}
                self._last_request_body = body
                return self.call_fn(body).strip()
            assert self.api_key is not None
            return self._consult_antigravity(payload.query, payload.category)
        except Exception as exc:  # noqa: BLE001
            self._last_failure_reason = f"{type(exc).__name__}: {exc}"
            return None

    def _consult_antigravity(self, query: str, category: str) -> str:
        tools = antigravity_tools_for_category(category)
        self._last_model = ANTIGRAVITY_AGENT
        self._last_request_body = {
            "agent": ANTIGRAVITY_AGENT,
            "input": query,
            "environment": "remote",
            "tools": tools,
            "category": category,
        }
        return default_antigravity_call(
            self.api_key or "",
            ANTIGRAVITY_AGENT,
            query,
            timeout=self.antigravity_timeout_seconds,
            tools=tools,
        ).strip()


def load_gemini_advisor(
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    antigravity_timeout_seconds: float = ANTIGRAVITY_TIMEOUT_SECONDS,
    call_fn: Callable[[dict], str] | None = None,
) -> GeminiAdvisorSkill:
    """Skill を構築。API キーは Core 側（factory）が `.env` から読んで渡す。
    キー無しでも無効 Skill を返す（会話は継続）。"""
    api_key = api_key or None
    if call_fn is not None:
        return GeminiAdvisorSkill(
            api_key=api_key,
            model=model,
            request_timeout_seconds=request_timeout_seconds,
            antigravity_timeout_seconds=antigravity_timeout_seconds,
            call_fn=call_fn,
        )
    if not api_key:
        return GeminiAdvisorSkill(api_key=None, model=model)
    return GeminiAdvisorSkill(
        api_key=api_key,
        model=model,
        request_timeout_seconds=request_timeout_seconds,
        antigravity_timeout_seconds=antigravity_timeout_seconds,
    )
