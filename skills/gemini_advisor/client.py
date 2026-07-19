"""Gemini / Antigravity API（アドバイザー専用・相談クエリのみ）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import requests

GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
INTERACTIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"

CONTENT_BLOCK_FINISH_REASONS = frozenset({
    "SAFETY", "PROHIBITED_CONTENT", "SPII", "BLOCKLIST",
})

# 当面は全 category を Antigravity 本線にする（Flash Search 枠がこのキーで不通のため）。
# 枠復活時の再配線用に連鎖だけ残置（skill からは呼ばない）。
WEB_MODEL_FALLBACK_CHAIN: tuple[str, ...] = (
    "gemini-3.5-flash",
    "gemini-3-flash-preview",  # Gemini 3 Flash
    "gemini-3.1-flash-lite",
)
DEFAULT_MODEL = WEB_MODEL_FALLBACK_CHAIN[0]

# Interactions API の managed agent
ANTIGRAVITY_AGENT = "antigravity-preview-05-2026"

DEFAULT_TIMEOUT_SECONDS = 45.0
ANTIGRAVITY_TIMEOUT_SECONDS = 180.0

# フォールバック対象の HTTP ステータス（レート制限・一時障害）
RETRYABLE_HTTP_STATUSES = frozenset({429, 503})

# generateContent 用 Google Search 接地（call_fn モック／枠復活時）
GOOGLE_SEARCH_TOOL = {"google_search": {}}

# Antigravity 用ツール
ANTIGRAVITY_SEARCH_TOOLS: list[dict] = [
    {"type": "google_search"},
    {"type": "url_context"},
]
ANTIGRAVITY_CODE_TOOLS: list[dict] = [{"type": "code_execution"}]


class GeminiAdvisorClientError(Exception):
    """アドバイザー API 応答の解釈失敗。"""


class AdvisorRejectionError(GeminiAdvisorClientError):
    """API 側の安全フィルタ・コンテンツブロックによる拒否。

    憲章 B-1（依存の一方向）のため Brain 層の CloudRejectionError は借りない。
    consult() が握って None にするため、上位層がこの型を捕まえる必要はない。
    """


def model_for_category(category: str | None) -> str:
    """当面は全 category が Antigravity。"""
    _ = category
    return ANTIGRAVITY_AGENT


def is_antigravity_agent(model_or_agent: str) -> bool:
    return model_or_agent.startswith("antigravity")


def wants_google_search(category: str | None) -> bool:
    """最新事実が要る相談は Search を付ける。"""
    key = (category or "general").strip().lower()
    return key in ("web_search", "general")


def antigravity_tools_for_category(category: str | None) -> list[dict]:
    key = (category or "general").strip().lower()
    if key == "code_qa":
        return list(ANTIGRAVITY_CODE_TOOLS)
    if wants_google_search(key):
        return list(ANTIGRAVITY_SEARCH_TOOLS)
    return list(ANTIGRAVITY_SEARCH_TOOLS)


def extract_reply_text(data: dict) -> str:
    """generateContent API JSON から素の回答テキストを取り出す。"""
    block_reason = (data.get("promptFeedback") or {}).get("blockReason")
    if block_reason:
        raise AdvisorRejectionError(f"Geminiがプロンプトを安全フィルタでブロック: {block_reason}")

    candidates = data.get("candidates") or []
    if not candidates:
        raise AdvisorRejectionError(f"Geminiが候補を返さなかった: {data}")

    finish_reason = candidates[0].get("finishReason")
    if finish_reason in CONTENT_BLOCK_FINISH_REASONS:
        raise AdvisorRejectionError(
            f"Geminiの応答がコンテンツブロックされた(finishReason={finish_reason})"
        )

    try:
        return candidates[0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as exc:
        raise GeminiAdvisorClientError(f"Gemini応答の形が想定外: {data}") from exc


def extract_interaction_text(data: dict) -> str:
    """Interactions API（Antigravity）JSON から最終回答テキストを取り出す。"""
    status = data.get("status")
    if status not in (None, "completed"):
        raise GeminiAdvisorClientError(f"Antigravity が未完了: status={status}")

    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    steps = data.get("steps") or []
    texts: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") != "model_output":
            continue
        content = step.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    t = part["text"].strip()
                    if t:
                        texts.append(t)
        elif isinstance(content, str) and content.strip():
            texts.append(content.strip())
    if texts:
        return texts[-1]
    raise GeminiAdvisorClientError(f"Antigravity 応答に model_output が無い: {data}")


def _is_retryable_http_error(exc: BaseException) -> bool:
    if not isinstance(exc, requests.HTTPError):
        return False
    response = exc.response
    return response is not None and response.status_code in RETRYABLE_HTTP_STATUSES


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


def default_gemini_call_with_fallback(
    api_key: str,
    models: Sequence[str],
    body: dict,
    *,
    timeout: float,
) -> tuple[str, str]:
    """models を優先順に試し、429/503 なら下位へ。戻り値は (text, used_model)。"""
    if not models:
        raise GeminiAdvisorClientError("フォールバック先モデルが空")
    last_error: BaseException | None = None
    for model in models:
        try:
            text = default_gemini_call(api_key, model, body, timeout=timeout)
            return text, model
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if _is_retryable_http_error(exc):
                continue
            raise
    assert last_error is not None
    raise last_error


def default_antigravity_call(
    api_key: str,
    agent: str,
    query: str,
    *,
    timeout: float,
    tools: list[dict] | None = None,
) -> str:
    """Antigravity managed agent へ送る（Interactions API）。"""
    body = {
        "agent": agent,
        "input": query,
        "environment": "remote",
        "tools": tools or [{"type": "code_execution"}],
    }
    response = requests.post(
        INTERACTIONS_ENDPOINT,
        headers={"x-goog-api-key": api_key},
        json=body,
        timeout=timeout,
    )
    response.raise_for_status()
    return extract_interaction_text(response.json())


def make_call_fn(
    api_key: str,
    *,
    model: str = DEFAULT_MODEL,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Callable[[dict], str]:
    """generateContent 用の薄いラッパ（テスト・単一モデル呼び出し向け）。"""

    def _call(body: dict) -> str:
        return default_gemini_call(api_key, model, body, timeout=timeout)

    return _call
