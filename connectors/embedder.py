"""埋め込み器インターフェースと Ollama bge-m3 実装（CPU）"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

import requests


@runtime_checkable
class Embedder(Protocol):
    """テキストを 1024 次元ベクトルに変換する"""

    def embed(self, text: str) -> list[float]:
        ...


class OllamaEmbedder:
    """Ollama 経由で bge-m3 を呼ぶ（Memory層は Ollama を直接 import しない＝store から注入）"""

    def __init__(
        self,
        model: str = "bge-m3",
        base_url: str = "http://127.0.0.1:11434",
        timeout: float = 120.0,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def embed(self, text: str) -> list[float]:
        if not text or not text.strip():
            raise ValueError("埋め込み対象のテキストが空です")

        response = requests.post(
            f"{self.base_url}/api/embed",
            json={"model": self.model, "input": text},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()

        # /api/embed は embeddings 配列、旧 /api/embeddings は embedding 単体
        embedding = None
        if "embeddings" in payload and payload["embeddings"]:
            embedding = payload["embeddings"][0]
        elif "embedding" in payload:
            embedding = payload["embedding"]

        if not embedding:
            raise RuntimeError(f"Ollama から embedding が返りませんでした: {json.dumps(payload)[:200]}")
        if len(embedding) != 1024:
            raise RuntimeError(f"期待次元 1024 に対し {len(embedding)} 次元が返りました")
        return [float(x) for x in embedding]


if __name__ == "__main__":
    import sys

    sample = sys.argv[1] if len(sys.argv) > 1 else "セリナの記憶テスト"
    embedder = OllamaEmbedder()
    vec = embedder.embed(sample)
    print(f"入力: {sample}")
    print(f"次元数: {len(vec)}")
    print(f"先頭5要素: {vec[:5]}")
