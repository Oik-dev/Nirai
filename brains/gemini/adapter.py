"""Gemini用通訳。設計書v2 §1.2, §3.4

条文A/B: 通訳は使い捨て状態（接続セッション等）以外を保持しない。文脈は毎回Coreから渡されるContextPackのみに従う。
自己評価欄は必須項目としてプロンプトで強制する（§3.4: 発火保証の要）。

実際のHTTP呼び出しは call_fn として注入可能にし、ユニットテストがネットワークに依存しないようにする。
既定の call_fn は実際にGemini APIを呼ぶ（tests/smoke_gemini.py等の実機確認でのみ使用）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import requests

from serina.core_v2.context.pack import ContextPack

GEMINI_ENDPOINT_TEMPLATE = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

SELF_ASSESSMENT_INSTRUCTION = (
    "この会話は自分の手に余るか？ はい／いいえ＋理由一言（自己評価欄は必須。省略不可）"
)

REPORT_FORMAT_INSTRUCTION = """
必ず次のJSON形式のみをコードブロックで返すこと（前後に説明文を含めてもよいが、JSON本体は改変しないこと）:
```json
{
  "reply": "マスターへの返答本文",
  "fusen_list": [
    {"kind": "付箋の種類名", "version": 1, "content": {}, "confidence": 0.0}
  ],
  "self_assessment": {"over_capacity": false, "reason": "理由一言"}
}
```
""".strip()


class GeminiAdapterError(Exception):
    """Gemini応答の解釈に失敗したことを示す例外。"""


class GeminiAdapter:
    def __init__(
        self,
        api_key: str,
        call_fn: Callable[[str], str] | None = None,
        model: str = "gemini-3.1-flash-lite",
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._call_fn = call_fn or self._default_call

    def build_prompt(self, pack: ContextPack) -> str:
        return (
            f"{pack.render()}\n\n"
            f"{REPORT_FORMAT_INSTRUCTION}\n\n"
            f"{SELF_ASSESSMENT_INSTRUCTION}\n"
        )

    def converse(self, pack: ContextPack) -> dict:
        prompt = self.build_prompt(pack)
        response_text = self._call_fn(prompt)
        return self._extract_report_dict(response_text)

    @staticmethod
    def _extract_report_dict(response_text: str) -> dict:
        match = re.search(r"```json\s*(\{.*?\})\s*```", response_text, re.DOTALL)
        candidate = match.group(1) if match else response_text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise GeminiAdapterError(f"Gemini応答からJSONを抽出できない: {e}") from e

    def _default_call(self, prompt: str) -> str:
        url = GEMINI_ENDPOINT_TEMPLATE.format(model=self._model)
        response = requests.post(
            url,
            params={"key": self._api_key},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise GeminiAdapterError(f"Gemini応答の形が想定外: {data}") from e
