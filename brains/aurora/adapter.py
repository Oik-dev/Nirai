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
返答本文は含めず、必ず次のJSON形式のみをコードブロックで返すこと（JSONブロックは1つだけにまとめ、複数のブロックに分けないこと）:
```json
{
  "fusen_list": [
    {"kind": "付箋の種類名", "version": 1, "content": {}, "confidence": 0.0}
  ],
  "self_assessment": {"over_capacity": false, "reason": "理由一言"}
}
```
fusen_listには複数の付箋を1つの配列にまとめて入れてよい。
""".strip()

# 設計書v2 §2.2/§3.3.1: センシティブ観測付箋。抽出発注（2回目）の時点で、
# 直前の会話全体を振り返って「この話題はローカル(自分)に向いていた／実は平気だった」を判定させる。
# keywordsは振り分けルールのラチェット（部分一致）の鍵になるため、話題そのものを特定する語だけを選ばせる。
# 実機smoke(2026-07-11)で「別JSONブロックとして書いてしまい抽出正規表現に握りつぶされる」不具合を確認したため、
# 「新しいJSONブロックを作らず、既存fusen_list配列に足す」ことを明示し、例もフェンスなしのインライン表記にする
# （フェンス付きコードブロックを増やすと、そちらが正規表現に先に拾われて本体の方が握りつぶされる）。
SENSITIVITY_OBSERVATION_INSTRUCTION = """
振り返って、この話題がクラウドには向かない（または逆に「実は平気」）と感じたら、
新しいJSONブロックを作らず、上のfusen_list配列の中に次の形式の要素を1つ追加すること（該当しなければ追加しなくてよい）:
{"kind": "センシティブ観測", "version": 1, "content": {"direction": "不向き", "keywords": ["話題を特定する語"]}, "confidence": 0.9}
directionは「不向き」または「平気」。keywordsは話題そのものを特定できる語だけを選ぶこと
（例: 「宮古島」「離婚」等の固有・具体的な語。「話」「相談」等の一般語は選ばない）。
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
        max_extraction_retries: int = 3,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._chat_call_fn = chat_call_fn or self._default_chat_call
        self._extract_call_fn = extract_call_fn or self._default_chat_call
        self._max_extraction_retries = max(1, max_extraction_retries)

    def build_chat_prompt(self, pack: ContextPack) -> str:
        """1回目: 自由に会話させる。書式強制はしない（RP特化の地力を活かす）。"""
        return pack.render()

    def build_extraction_prompt(self, pack: ContextPack, stage1_reply: str) -> str:
        """2回目: 直前の会話（マスター発言＋Auroraの返答）から付箋だけを抜き出す別発注。"""
        return (
            f"【今回のマスターの発言】\n{pack.master_utterance}\n\n"
            f"【Auroraの返答】\n{stage1_reply}\n\n"
            f"{EXTRACTION_FORMAT_INSTRUCTION}\n\n"
            f"{SENSITIVITY_OBSERVATION_INSTRUCTION}\n"
        )

    def converse(self, pack: ContextPack) -> dict:
        stage1_reply = self._chat_call_fn(self.build_chat_prompt(pack))
        extraction_prompt = self.build_extraction_prompt(pack, stage1_reply)

        extracted = self._extract_with_retry(extraction_prompt)
        self_assessment = extracted.get("self_assessment")
        if not isinstance(self_assessment, dict):
            # §5.5-7: Auroraの書式弱点は通訳が吸収する。§3.2最終防衛線は契約書式を必ず満たす必要がある
            self_assessment = {"over_capacity": False, "reason": "自己評価を抽出できず安全側の既定値で補った"}

        return {
            "reply": stage1_reply,
            "fusen_list": extracted.get("fusen_list", []),
            "self_assessment": self_assessment,
        }

    def _extract_with_retry(self, extraction_prompt: str) -> dict:
        """§5.5-7: Auroraは書式が苦手なため、2回目の抽出は失敗しても数回リトライする。"""
        last_error: AuroraAdapterError | None = None
        for _ in range(self._max_extraction_retries):
            extraction_text = self._extract_call_fn(extraction_prompt)
            try:
                return self._extract_json(extraction_text)
            except AuroraAdapterError as e:
                last_error = e
        raise last_error  # type: ignore[misc]

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
