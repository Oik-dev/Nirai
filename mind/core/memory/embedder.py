"""記憶検索用の埋め込み(bge-m3, Ollama)。新規実装（旧connectors/embedder.pyは参照しない・Core内臓の一部 §1.3）"""

from __future__ import annotations

from collections.abc import Callable

from mind.brains.ollama import serve

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
        request_timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._call_fn = call_fn or self._default_call
        self._request_timeout_seconds = request_timeout_seconds

    def embed(self, text: str) -> list[float]:
        vector = self._call_fn(self._model, text)
        if not vector:
            raise EmbedderError(f"空の埋め込みベクトルが返された: model={self._model}")
        return vector

    def warm(self) -> None:
        """埋め込みのモデルを載せておく（替え玉の call_fn のときは何もしない）。"""
        if self._call_fn == self._default_call:
            serve.warm(f"{self._base_url}/api/embeddings", self._payload(self._model, ""))

    def _payload(self, model: str, text: str) -> dict:
        return {
            "model": model,
            "prompt": text,
            # bge-m3はCPU席固定（設計書の技術スタック「埋め込み bge-m3(CPU)」前提どおり）。GPUに載せると8GB VRAM上で
            # 会話Brain(35B)と席を取り合い、想起のたびに35Bが退避→再ロード(約30秒)される
            # （2026-07-20 実機ログで毎ターン発生を確認）。CPU常駐（載せたままは serve が決める）なら
            # RAM約1GBで衝突せず、短文クエリの埋め込みはCPUでも1秒未満。
            "options": {"num_gpu": 0},
        }

    def _default_call(self, model: str, text: str) -> list[float]:
        response = serve.post(
            f"{self._base_url}/api/embeddings",
            json=self._payload(model, text),
            timeout=self._request_timeout_seconds,
        )
        response.raise_for_status()
        data = response.json()
        embedding = data.get("embedding")
        if not embedding:
            raise EmbedderError(f"Ollama応答に埋め込みが含まれない: {data}")
        return embedding
