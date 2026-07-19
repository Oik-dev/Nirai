"""Gemini generateContent API（アドバイザー専用・相談クエリのみ）。"""

from __future__ import annotations

from collections.abc import Callable

import requests

from serina.brains.contract.schema import CloudRejectionError

GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

CONTENT_BLOCK_FINISH_REASONS = frozenset({
    "SAFETY", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
})

DEFAULT_MODEL = "gemini-2.0-flash"
DEFAULT_TIMEOUT_SECONDS = 30.0


class GeminiAdvisorClientError(Exception):
    """アドバイザー API 応答の解釈失敗。"""


def extract_reply_text(data: dict) -> str:
    """API JSON から素の回答テキストを取り出す。"""
    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise CloudRejectionError(f"Geminiがプロンプトを安全フィルタでブロック: {block_reason}")

    candidates = data.get("candidates") or []
    if not candidates:
        raise CloudRejectionError(f"Geminiが候補を返さなかった: {data}")

    finish_reason = candidates[0].get("finishReason")
    if finish_reason in CONTENT_BLOCK_FINISH_REASONS:
        raise CloudRejectionError(
            f"Geminiの応答がコンテンツブロックされた(finishReason={finish_reason})"
        )

    try:
        return candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise GeminiAdvisorClientError(f"Gemini応答の形が想定外: {data}") from exc


def default_gemini_call(api_key: str, model: str, body: dict, *, timeout: float) -> str:
    url = GEMINI_ENDPOINT_TEMPLATE.format(model=model)
    response = requests.post(
        url,
        headers={"x-goog-api-key": api_key},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return extract_reply_text(response.json())


def make_call_fn(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[dict], str]:
    def _call(body: dict) -> str:
        return default_gemini_call(api_key, model, body, timeout=timeout)

    return _call
