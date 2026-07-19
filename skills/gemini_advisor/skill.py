"""Gemini 無人格アドバイザー Skill。会話 Brain ではない（GO §3.1）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from serina.skills.gemini_advisor.client import DEFAULT_MODEL, DEFAULT_TIMEOUT_SECONDS, make_call_fn
from serina.skills.gemini_advisor.instructions import system_instruction_for
from serina.skills.gemini_advisor.payload import AdvisorPayload, SensitivityRules, build_payload


@dataclass
class GeminiAdvisorSkill:
    """相談クエリのみを Gemini へ送る無人格アドバイザー。"""

    api_key: str | None = None
    model: str = DEFAULT_MODEL
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    call_fn: Callable[[dict], str] | None = None
    _last_request_body: dict = field(default_factory=dict, repr=False)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key or self.call_fn)

    @property
    def last_request_body(self) -> dict:
        """直近 consult の API body（テスト・監査用）。"""
        return dict(self._last_request_body)

    def consult(
        self,
        query: str,
        *,
        category: str = "general",
        routing_rules: SensitivityRules | None = None,
    ) -> str | None:
        """相談クエリを送り素の回答を返す。失敗・無効時は None（会話は継続）。"""
        if not self.enabled:
            return None
        payload = build_payload(query, category=category, routing_rules=routing_rules)
        if payload is None:
            return None
        system = system_instruction_for(payload.category)
        body = payload.to_api_body(system_instruction=system)
        self._last_request_body = body
        try:
            if self.call_fn is not None:
                return self.call_fn(body).strip()
            assert self.api_key is not None
            fn = make_call_fn(
                self.api_key,
                model=self.model,
                timeout=self.request_timeout_seconds,
            )
            return fn(body).strip()
        except Exception:  # noqa: BLE001
            return None


def load_gemini_advisor(
    *,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    request_timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    call_fn: Callable[[dict], str] | None = None,
) -> GeminiAdvisorSkill:
    """Skill を構築。API キーは Core 側（factory）が `.env` から読んで渡す（設計書 §1.3 の下り一方向）。
    キー無しでも無効 Skill を返す（会話は継続）。"""
    api_key = api_key or None
    if call_fn is not None:
        return GeminiAdvisorSkill(
            api_key=api_key,
            model=model,
            request_timeout_seconds=request_timeout_seconds,
            call_fn=call_fn,
        )
    if not api_key:
        return GeminiAdvisorSkill(api_key=None, model=model)
    return GeminiAdvisorSkill(
        api_key=api_key,
        model=model,
        request_timeout_seconds=request_timeout_seconds,
    )
