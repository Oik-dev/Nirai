"""手元のGemmaに、JSONで答えてもらう（会話ではない仕事：記憶の作り直し、記憶テストの判定など）。

会話用の通訳（adapter.py）と同じモデルを使う。ただし、同じ問いに同じ答えが返ってほしいので、
温度0（聞き直すときは呼ぶ側が温度を上げる）・種固定・JSON出力をここで指定する。人格は、必要なら問いの中に入れる。
答えの形（JSON Schema）を渡せば、脳はその形でしか答えられない（Ollama の構造化出力）。
"""

from __future__ import annotations

import json

import requests

from mind.brains.ollama.adapter import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_NUM_CTX

TIMEOUT_SECONDS = 600.0
MAX_TOKENS = 2048  # 答えが空白や繰り返しで止まらなくなったときの歯止め


def ask_json(
    prompt: str,
    *,
    schema: dict | None = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    temperature: float = 0.0,
) -> dict:
    response = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "format": schema or "json",
            "options": {"num_ctx": DEFAULT_NUM_CTX, "temperature": temperature, "seed": 0, "num_predict": MAX_TOKENS},
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    answer = json.loads(response.json()["response"])
    if not isinstance(answer, dict):
        raise ValueError("JSONのオブジェクトではない")
    return answer
