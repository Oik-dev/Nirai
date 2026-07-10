"""Aurora用通訳（ローカル・Ollama経由・NemoAurora-RP-12B）。設計書v2 §1.2, §5.5-7

既知の最大リスク対策: Aurora（RP特化）は書式厳守が苦手なので二段方式にする。
1回目: 自由に会話させる（書式強制なし）。
2回目: 直前の会話から付箋だけを抜き出す小さな作業を別途発注（遅くても許容・裏方寄りの処理）。
この対策は通訳の内部に閉じ、Core・契約書式には影響しない（条文Aに抵触しない）。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import requests

from serina.core_v2.context.pack import ContextPack

DEFAULT_MODEL = "hf.co/Aratako/NemoAurora-RP-12B-GGUF:IQ4_XS"
DEFAULT_BASE_URL = "http://localhost:11434"

EXTRACTION_FORMAT_INSTRUCTION = """
直前の会話から、報告書の付箋束と自己評価欄だけを抜き出してください。
返答本文は含めず、必ず次のJSON形式のみをコードブロックで返すこと:
```json
{
  "fusen_list": [
    {"kind": "付箋の種類名", "version": 1, "content": {}, "confidence": 0.0}
  ],
  "self_assessment": {"over_capacity": false, "reason": "理由一言"}
}
```
""".strip()


class AuroraAdapterError(Exception):
    """Aurora応答の解釈に失敗したことを示す例外。"""


class AuroraAdapter:
    def __init__(
        self,
        chat_call_fn: Callable[[str], str] | None = None,
        extract_call_fn: Callable[[str], str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._chat_call_fn = chat_call_fn or self._default_chat_call
        self._extract_call_fn = extract_call_fn or self._default_chat_call

    def build_chat_prompt(self, pack: ContextPack) -> str:
        """1回目: 自由に会話させる。書式強制はしない（RP特化の地力を活かす）。"""
        return pack.render()

    def build_extraction_prompt(self, pack: ContextPack, stage1_reply: str) -> str:
        """2回目: 直前の会話（マスター発言＋Auroraの返答）から付箋だけを抜き出す別発注。"""
        return (
            f"【今回のマスターの発言】\n{pack.master_utterance}\n\n"
            f"【Auroraの返答】\n{stage1_reply}\n\n"
            f"{EXTRACTION_FORMAT_INSTRUCTION}\n"
        )

    def converse(self, pack: ContextPack) -> dict:
        stage1_reply = self._chat_call_fn(self.build_chat_prompt(pack))
        extraction_prompt = self.build_extraction_prompt(pack, stage1_reply)
        extraction_text = self._extract_call_fn(extraction_prompt)
        extracted = self._extract_json(extraction_text)

        return {
            "reply": stage1_reply,
            "fusen_list": extracted.get("fusen_list", []),
            "self_assessment": extracted.get("self_assessment"),
        }

    @staticmethod
    def _extract_json(text: str) -> dict:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = match.group(1) if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise AuroraAdapterError(f"Aurora応答(2回目)からJSONを抽出できない: {e}") from e

    def _default_chat_call(self, prompt: str) -> str:
        response = requests.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": False},
            timeout=120,
        )
        response.raise_for_status()
        data = response.json()
        text = data.get("response")
        if not text:
            raise AuroraAdapterError(f"Ollama応答にresponseが含まれない: {data}")
        return text
