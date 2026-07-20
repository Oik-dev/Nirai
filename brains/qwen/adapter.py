"""Qwen用通訳（ローカル・Ollama経由・Qwen3.5-35B-A3B-Uncensored）。

合意台帳 §9・§6-1・§7.1。会話の主戦力を1本化する新設Brain。

converseの返答本文（reply）は**単発呼び**で得る。Gemini式の「1回の呼び出しで報告書JSON
全体（reply/fusen_list/self_assessment）を書かせる」方式は、返答本文についてのみ採らない。
理由:

- §6-1実機スモークのJSON妥当性11/11は「persona非注入・neutral prompt」条件での計測値。
  persona注入下（設計書§1.5相当のフルパックを渡す経路）でのJSON遵守率はスモーク未測定
  （観測されたのは注入下で300〜400tok超のprose長文化傾向のみ）。
- 単一Brain運用ではfallbackが実質primary自身であるため、返答生成そのものが書式違反を
  返すとcore.runtime.Core._obtain_valid_reportが合成の詫び文言（_minimal_raw_report）へ
  落ち、モデルが実際に生成した返答本文が握りつぶされる。RP傾向の強いモデルでこれを
  起こす設計は会話品質そのものを損なう。

一方、**感情の動き（心の動き付箋）はEmotionStateの唯一の更新経路**（`core/intake/gate.py`
の`_apply_fusen`）であり、`fusen_list`を常に空にすると人格資産の核である感情表現が
起動時状態のまま凍結する実害がある（設計書§2.3・§2.6）。よってconverseは**返答生成とは
別の軽量な感情報告**で「心の動き」「マスター観測」付箋だけを抽出する。感情報告の手順は
通常会話・事実レーンで共通（1本）。外聞き要否の自律判定（旧第3発注）は廃止し、外聞きは
事実レーン（Core規則）のみ。感情報告はpersona非注入・think:false。失敗しても例外を
外へ漏らさずfusen_list=[]で継続する（会話を止めない。§2.4の裏方原則を即時便にも適用）。

judgeはRecallPlanner／think ON-OFF判定（§3.1改訂）が使う。persona非注入・
think:false固定でJSON応答を期待する構成（§6-1実機スモークで妥当性を確認した構成を踏襲）。

全メソッド既定think:false（§6-1「技術的注意」: think有効時は隠れ思考で体感速度が大きく
劣化する。think:false明示で概ね24.6 tok/s・1ターン15〜21秒まで改善）。

2026-07-20 応答高速化:
- converseはon_token（返答本文のトークン小出し）・on_reply（本文確定通知）を受ける。
  on_reply発火後に感情報告を行う。デフォルトOllama呼びのみstream:trueで小出しに対応。
- 事実レーンのadvisor結果はcompose_advisor_followup（2通目）で配達する。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

import requests

from serina.core.context.pack import ContextPack
from serina.core.state.emotion import PLUTCHIK_AXES

DEFAULT_MODEL = "serina-qwen35-unc"
DEFAULT_BASE_URL = "http://localhost:11434"

DEFAULT_SELF_ASSESSMENT = {
    "over_capacity": False,
    "reason": "品質昇格機構は廃止済み（§2.3・§9.2）。単一Brain運用の既定値",
}

# 設計書§2.3/§2.6・core/state/emotion.pyのPLUTCHIK_AXESと一致させること。
EMOTION_EXTRACTION_INSTRUCTION = """
直前のやり取りから、セリナの感情の動きとマスターの様子だけを抜き出してください。
返答本文は不要です。必ず次のJSON形式のみをコードブロックで返すこと（該当がなければ
fusen_listは空配列でよい。JSONブロックは1つだけにまとめること）:
```json
{
  "fusen_list": [
    {"kind": "心の動き", "version": 1,
     "content": {"deltas": {"喜び": 0.0, "信頼": 0.0, "恐れ": 0.0, "驚き": 0.0,
                             "悲しみ": 0.0, "嫌悪": 0.0, "怒り": 0.0, "期待": 0.0},
                  "きっかけ": "一言"},
     "confidence": 0.8},
    {"kind": "マスター観測", "version": 1,
     "content": {"observation": "疲れてそう、等の一言観測"}, "confidence": 0.8}
  ]
}
```
deltasは動いた軸だけを含めてよい（変化が無い軸は省略可）。各値は-1.0〜1.0の増減量。
和解・謝罪・フォローのやりとりでは、怒り・悲しみ・恐れなど負の情動を減らすΔを出してよい。
嬉しい・親密なやりとりでは喜び・信頼・期待などを増やしてよい。
心の動き・マスター観測のいずれかが無ければ、その要素自体をfusen_listから省くこと。
""".strip()

ADVISOR_FOLLOWUP_INSTRUCTION = """
あなたはセリナです。直前の返答はもうマスターに届いています。その続きとして、
アドバイザーから得た材料（無人格・素の事実）をマスターへ伝える「2通目の短いメッセージ」を書いてください。
アドバイザーの原文をそのまま引用せず、セリナの口調で自然な会話に溶かすこと。人格・口調はパックの persona に従うこと。
1通目と同じ内容の繰り返しは不要。材料が空・失敗の場合は、調べきれなかったことを短く正直に伝えてください。
返答本文だけを出力してください（JSON不要）。
""".strip()


class QwenAdapterError(Exception):
    """Qwen応答の解釈に失敗したことを示す例外。"""


class QwenAdapter:
    def __init__(
        self,
        chat_call_fn: Callable[[str], str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        request_timeout_seconds: float = 240.0,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._uses_default_chat = chat_call_fn is None
        self._chat_call_fn = chat_call_fn or self._default_chat_call
        self._request_timeout_seconds = request_timeout_seconds

    def build_chat_prompt(self, pack: ContextPack) -> str:
        """返答生成: 書式強制はしない。パックをそのまま渡す。"""
        return pack.render()

    def build_emotion_extraction_prompt(self, pack: ContextPack, serina_text: str) -> str:
        """感情報告: 心の動き・マスター観測だけを抜き出す（persona非注入）。手順は1本。"""
        return (
            f"【今回のマスターの発言】\n{pack.master_utterance}\n\n"
            f"【セリナの返答】\n{serina_text}\n\n"
            f"{EMOTION_EXTRACTION_INSTRUCTION}\n"
        )

    def build_advisor_followup_prompt(
        self,
        pack: ContextPack,
        stage1_reply: str,
        advisor_results: list[dict],
    ) -> str:
        """アドバイザー結果を2通目メッセージへ翻訳する（persona注入）。事実レーン専用。"""
        lines = []
        for item in advisor_results:
            lines.append(f"相談: {item.get('query', '')}\n回答: {item.get('answer', '')}")
        advisor_block = "\n\n".join(lines) if lines else "（アドバイザー回答なし）"
        return (
            f"{pack.render()}\n\n"
            f"【セリナの1通目（送信済み）】\n{stage1_reply}\n\n"
            f"【アドバイザーからの材料】\n{advisor_block}\n\n"
            f"{ADVISOR_FOLLOWUP_INSTRUCTION}\n"
        )

    def raw_call(self, prompt: str) -> str:
        """会話用ではない素の生成呼び出し。蒸留消化(裏方便)のlane_call_fnとして再利用する
        （core/chores/orchestrator.py）。DI済みのchat_call_fn(テスト用差し替え含む)をそのまま使う。"""
        return self._chat_call_fn(prompt)

    def converse(
        self,
        pack: ContextPack,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
    ) -> dict:
        """返答生成→（on_reply通知）→感情報告の順で1ターン分の報告書を作る。

        on_token/on_replyはGUIストリーミング用（2026-07-20）。on_replyは返答本文の確定直後・
        感情報告の前に呼ぶ。空返答のときは通知しない。
        外聞き要否の自律判定（旧第3発注）は廃止。advisor_tool_callsは常に空。
        """
        reply = self._chat_call_with_think(
            self.build_chat_prompt(pack), think=think, on_token=on_token,
        ).strip()
        if reply and on_reply is not None:
            on_reply(reply)
        return {
            "reply": reply,
            "fusen_list": self.extract_emotion_fusen(pack, reply),
            "advisor_tool_calls": [],
            "self_assessment": dict(DEFAULT_SELF_ASSESSMENT),
        }

    def compose_advisor_followup(
        self,
        pack: ContextPack,
        stage1_reply: str,
        advisor_results: list[dict],
        *,
        think: bool = False,
    ) -> str:
        """アドバイザー材料を2通目メッセージへ翻訳する。失敗・材料なしは空文字（2通目なし）。

        旧rephrase_with_advisor（1通目の置換）はストリーミング導入で退役: 1通目は表示済みの
        ため置換できず、結果は追伸として届ける（2026-07-20承認の方式変更）。
        """
        if not advisor_results:
            return ""
        try:
            prompt = self.build_advisor_followup_prompt(pack, stage1_reply, advisor_results)
            return self._chat_call_with_think(prompt, think=think).strip()
        except Exception:  # noqa: BLE001
            return ""

    def _chat_call_with_think(
        self,
        prompt: str,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        """返答生成用。第2・第3発注は常に think=False。

        DI差し替え（テスト・蒸留のlane_call_fn）はthink/streamの概念を持たないため
        on_tokenは黙って無視される（一括応答）。呼び出し元はon_reply通知で吸収する。
        """
        if self._uses_default_chat:
            return self._default_chat_call(prompt, think=think, on_token=on_token)
        return self._chat_call_fn(prompt)

    def extract_emotion_fusen(self, pack: ContextPack, serina_text: str) -> list[dict]:
        """感情報告（手順は1本）。通常会話も事実レーンもこれを呼ぶ。失敗時は空配列。

        §5.5-7: 書式強制なしの自由生成（uncensored RPモデル）は幻覚キーの混入があり得る
        （「喜び」の代わりに「幸福」等）。対策は二段:

        1. ここ（通訳内部）で既知のプルチック8軸以外のキーを落としてから返す（一次防壁）
        2. `EmotionState.apply_*_delta` も未知軸を無視する（二次防壁・2026-07-19）

        関所①（書式）は `core/intake/gate.py` が別途担う。
        """
        try:
            extraction_text = self._chat_call_with_think(
                self.build_emotion_extraction_prompt(pack, serina_text),
                think=False,
            )
            parsed = self._extract_json(extraction_text)
        except Exception:  # noqa: BLE001
            return []
        fusen_list = parsed.get("fusen_list")
        if not isinstance(fusen_list, list):
            return []
        sanitized = (self._sanitize_fusen(f) for f in fusen_list)
        return [f for f in sanitized if f is not None]

    def _extract_emotion_fusen(self, pack: ContextPack, serina_text: str) -> list[dict]:
        """後方互換エイリアス。"""
        return self.extract_emotion_fusen(pack, serina_text)

    @staticmethod
    def _sanitize_fusen(fusen: object) -> dict | None:
        """幻覚キー・型崩れを持つ付箋を無害化する。壊れすぎて直せないものはNoneで捨てる
        （書式検査そのもの＝関所①はcore/intake/gate.pyが別途行うため、ここでは
        「Core側の下流処理がKeyError等で落ちる」既知の穴だけを塞ぐ）。
        """
        if not isinstance(fusen, dict):
            return None
        kind = fusen.get("kind")
        if kind != "心の動き":
            return fusen
        content = fusen.get("content")
        if not isinstance(content, dict):
            return fusen
        deltas = content.get("deltas")
        if not isinstance(deltas, dict):
            return fusen
        safe_deltas = {
            axis: value
            for axis, value in deltas.items()
            if axis in PLUTCHIK_AXES and isinstance(value, (int, float))
        }
        return {**fusen, "content": {**content, "deltas": safe_deltas}}

    def judge(self, prompt: str) -> dict:
        """persona非注入・think:false固定の判定発注(下ごしらえ)。呼び出し元は今回実装しない。

        将来のRecallPlanner（§3.1）・think ON/OFF判定（§3.1改訂）が、persona非注入の
        neutral promptを渡してJSON応答を受け取る想定。呼び出しに使うcall_fn自体は
        converse/raw_callと共有する（think:falseは_default_chat_callに既定で載っている
        ため、呼び出し元ごとに個別設定する必要がない）。
        """
        response_text = self._chat_call_fn(prompt)
        return self._extract_json(response_text)

    @staticmethod
    def _extract_json(text: str) -> dict:
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = match.group(1) if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise QwenAdapterError(f"Qwen応答からJSONを抽出できない: {e}") from e

    def _default_chat_call(
        self,
        prompt: str,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        if on_token is None:
            response = requests.post(
                f"{self._base_url}/api/generate",
                json={"model": self._model, "prompt": prompt, "stream": False, "think": think},
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("response")
            if not text:
                raise QwenAdapterError(f"Ollama応答にresponseが含まれない: {data}")
            return text

        # stream:true はNDJSON行の逐次到着。"response"は可視トークンのみで、think時の
        # 隠れ思考は"thinking"側に分離されるため、そのまま画面へ流してよい。
        # timeoutはチャンク間の無応答ガードとして働く（総時間ではない）。
        parts: list[str] = []
        with requests.post(
            f"{self._base_url}/api/generate",
            json={"model": self._model, "prompt": prompt, "stream": True, "think": think},
            timeout=self._request_timeout_seconds,
            stream=True,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                chunk = data.get("response") or ""
                if chunk:
                    parts.append(chunk)
                    try:
                        on_token(chunk)
                    except Exception:  # noqa: BLE001 — 表示側の失敗で生成を止めない
                        on_token = lambda _chunk: None  # noqa: E731
                if data.get("done"):
                    break
        text = "".join(parts)
        if not text:
            raise QwenAdapterError("Ollamaストリーム応答が空だった")
        return text
