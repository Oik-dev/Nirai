"""手元のGemmaへの問い合わせ（思い出したものが話に関係あるかの判定と、今の記憶の想起の計画）。

会話用の通訳（brains/ollama/adapter.py）と同じモデルを使う。ただしテストは同じ問いに同じ答えが返ってほしいので、
温度0・種固定・JSON出力をここで指定する。人格は渡さない。
"""

from __future__ import annotations

import json

import requests

from mind.brains.ollama.adapter import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_NUM_CTX

TIMEOUT_SECONDS = 600.0


def ask_json(prompt: str, *, model: str = DEFAULT_MODEL, base_url: str = DEFAULT_BASE_URL) -> dict:
    response = requests.post(
        f"{base_url}/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"num_ctx": DEFAULT_NUM_CTX, "temperature": 0, "seed": 0},
        },
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    answer = json.loads(response.json()["response"])
    if not isinstance(answer, dict):
        raise ValueError("JSONのオブジェクトではない")
    return answer
