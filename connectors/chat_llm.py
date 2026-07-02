"""対話 LLM Connector（Ollama /api/chat）"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

import requests


@runtime_checkable
class ChatConnector(Protocol):
    def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
    ) -> str:
        ...


class OllamaChatConnector:
    """Ollama 経由で対話モデルを呼ぶ。ビジネスロジックは持たない。"""

    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 300.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        options: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
        }
        if options:
            payload["options"] = options

        response = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        message = data.get("message") or {}
        content = message.get("content", "")
        if not content or not str(content).strip():
            raise RuntimeError(f"Ollama から空の応答: {json.dumps(data)[:300]}")
        return str(content)

    @staticmethod
    def list_models(base_url: str = "http://127.0.0.1:11434") -> list[str]:
        response = requests.get(f"{base_url.rstrip('/')}/api/tags", timeout=30.0)
        response.raise_for_status()
        models = response.json().get("models") or []
        return [m.get("name", "") for m in models if m.get("name")]

    @classmethod
    def ensure_model_available(
        cls,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
    ) -> None:
        available = cls.list_models(base_url)
        if model not in available:
            raise RuntimeError(
                f"Ollama にモデル '{model}' がありません。"
                f" 利用可能: {', '.join(available) or '(なし)'}"
            )
