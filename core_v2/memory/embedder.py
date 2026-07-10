"""記憶検索用の埋め込み(bge-m3, Ollama)。新規実装（旧connectors/embedder.pyは参照しない・Core内臓の一部 §1.3）"""

from __future__ import annotations

from collections.abc import Callable

import requests

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "bge-m3"


class EmbedderError(Exception):
    """埋め込み取得に失敗したことを示す例外。"""


class OllamaEmbedder:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        call_fn: Callable[[str, str], list[float]] | None = None,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._call_fn = call_fn or self._default_call

    def embed(self, text: str) -> list[float]:
        vector = self._call_fn(self._model, text)
        if not vector:
            raise EmbedderError(f"空の埋め込みベクトルが返された: model={self._model}")
        return vector

    def _default_call(self, model: str, text: str) -> list[float]:
        response = requests.post(
            f"{self._base_url}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        embedding = data.get("embedding")
        if not embedding:
            raise EmbedderError(f"Ollama応答に埋め込みが含まれない: {data}")
        return embedding
