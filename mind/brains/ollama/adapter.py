"""Ollama用通訳（ローカル・Ollama経由。既定モデルは`DEFAULT_MODEL`で可変）。

設計書 §3.1。会話の主戦力を1本化するBrain（現行既定モデル: Gemma4-26B-A4B-uncensored）。

converseの返答本文（reply）は**単発呼び**で得る。Gemini式の「1回の呼び出しで報告書JSON
全体を書かせる」方式は、返答本文についてのみ採らない。理由:

- §6-1実機スモークのJSON妥当性11/11は「persona非注入・neutral prompt」条件での計測値。
  persona注入下（設計書§1.5相当のフルパックを渡す経路）でのJSON遵守率はスモーク未測定
  （観測されたのは注入下で300〜400tok超のprose長文化傾向のみ）。
- 単一Brain運用ではfallbackが実質primary自身であるため、返答生成そのものが書式違反を
  返すとcore.runtime.Core._obtain_valid_reportが合成の詫び文言（_minimal_raw_report）へ
  落ち、モデルが実際に生成した返答本文が握りつぶされる。RP傾向の強いモデルでこれを
  起こす設計は会話品質そのものを損なう。

返答のあとに、**本人の評価**（今のやりとりをどう感じたか。設計書§2.3、core/feeling/appraisal.py）を
別に聞く。問いは文脈パックと返答の**後ろ**に足すので、前置きが返答のときと同じになり、Ollamaがその計算を
使い回せる（2026-10-05 E1で、返答の前に聞くより合計で速かった）。答えの形はJSON Schemaで縛り（構造化出力）、
温度0で聞く。数は書かせない。失敗しても例外を外へ漏らさず appraisal=None で続ける（会話を止めない。
§2.4の裏方原則を即時便にも適用）。体のカタログ（core/perception.py）を渡されたら、問いの先頭で体の欄も聞き、答えを流しながら読んで、
体の欄が閉じた時点で on_body へ先に渡す（評価の全体を待たずに表情が変わる。体の欄の失敗は評価に響かない）。外聞き要否の自律判定（旧第3発注）は廃止し、外聞きは事実レーン（Core規則）のみ。

judgeはthink ON-OFF判定とTavily検索要否判定が使う。persona非注入・
think:false固定でJSON応答を期待する構成（§6-1実機スモークで妥当性を確認した構成を踏襲）。

全メソッド既定think:false（§6-1「技術的注意」: think有効時は隠れ思考で体感速度が大きく
劣化する。think:false明示で概ね24.6 tok/s・1ターン15〜21秒まで改善）。

2026-07-20 応答高速化:
- converseはon_token（返答本文のトークン小出し）・on_reply（本文確定通知）を受ける。
  on_reply発火後に評価を聞く。デフォルトOllama呼びのみstream:trueで小出しに対応。

2026-07-31 Phase D/E: Gemini/Tavily窓口の結果はConverse呼び出し**前**にCoreが確定させ、
packの【外部情報（今回のみ）】節へ材料として載せる（無言統合パイプライン）。旧
compose_advisor_followup（2通目メッセージ生成）は退役済み。1ターン=converse1回で完結する。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from mind.brains.ollama import serve
from mind.core.chores.pulse import PULSE_CHOICE_SCHEMA
from mind.core.context.pack import ContextPack
from mind.core.feeling.appraisal import appraisal_question, appraisal_schema
from mind.core.perception import BodyCatalog, body_alone_question, body_alone_schema, body_schema

DEFAULT_MODEL = "serina-gemma4-unc"
DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_NUM_CTX = 8192
DEFAULT_USE_MMAP = True
APPRAISAL_MAX_TOKENS = 256  # E1で、40では評価のJSONの末尾が切れ、120で足りた
STEADY = {"temperature": 0.0, "seed": 0}  # 評価と体の欄（同じ場面に同じ答え）。言葉を書かせる問いには付けない（返事と同じ温度）

class OllamaAdapterError(Exception):
    """Ollama応答の解釈に失敗したことを示す例外。"""


class MotionDescribeUnavailable(Exception):
    """動作説明の生成中に脳との通信ができなかった（願いの失敗ではない）。"""


class MotionDescribeInvalid(Exception):
    """脳の動作説明が所定の形を満たさなかった（願い固有の失敗）。"""


MOTION_DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {
        "text": {"type": "string"},
        "seconds": {"type": "integer", "minimum": 1, "maximum": 10},
    },
    "required": ["text", "seconds"],
    "additionalProperties": False,
}


def closed_fields(text: str) -> dict:
    """届いた途中の JSON の文字から、閉じた欄だけを取り出す（書いている途中の欄と、壊れた文字は含めない）。"""
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    in_string = escaped = False
    closed = ""  # 閉じた欄までの文字。閉じかっこを足せば JSON になる
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                closed = text[start:i]
                break
        elif ch == "," and depth == 1:
            closed = text[start:i]
    try:
        fields = json.loads(closed + "}") if closed else {}
    except json.JSONDecodeError:
        return {}
    return fields if isinstance(fields, dict) else {}


class _BodyFields:
    """流れてくる評価の答えを聞き、体の欄がそろった時点で1回だけ on_body へ渡す。"""

    def __init__(self, catalog: BodyCatalog | None, on_body: Callable[[dict], None] | None) -> None:
        # wish は gesture の直後に流れてくる。閉じる前に送ると新しい願いを取りこぼす。
        self._keys = tuple(body_schema(catalog, include_activity=True))
        self._on_body = on_body if self._keys else None
        self._text = ""

    def __call__(self, chunk: str) -> None:
        if self._on_body is None:
            return
        self._text += chunk
        fields = closed_fields(self._text)
        if all(key in fields for key in self._keys):
            on_body, self._on_body = self._on_body, None
            try:
                on_body({key: fields[key] for key in self._keys})
            except Exception:  # noqa: BLE001 — 体の欄の失敗で評価を止めない
                pass


class OllamaAdapter:
    def __init__(
        self,
        chat_call_fn: Callable[[str], str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        request_timeout_seconds: float = 240.0,
        num_ctx: int = DEFAULT_NUM_CTX,
        use_mmap: bool = DEFAULT_USE_MMAP,
    ) -> None:
        self._base_url = base_url
        self._model = model
        self._uses_default_chat = chat_call_fn is None
        self._chat_call_fn = chat_call_fn or self._default_chat_call
        self._request_timeout_seconds = request_timeout_seconds
        self._num_ctx = num_ctx
        self._use_mmap = use_mmap

    def build_chat_prompt(self, pack: ContextPack) -> str:
        """返答生成: 書式強制はしない。パックをそのまま渡す。"""
        return pack.render()

    def build_appraisal_prompt(self, pack: ContextPack, reply: str, catalog: BodyCatalog | None = None) -> str:
        """評価: 返答のときと同じ前置き（パック）の後ろに、返した言葉と評価の問い（カタログがあれば体の欄つき）を足す。"""
        return f"{self.build_chat_prompt(pack)}\n【あなたが今返した言葉】\n{reply}\n\n{appraisal_question(catalog)}\n"

    def raw_call(self, prompt: str) -> str:
        """会話用ではない素の生成呼び出し。裏方（会話の要約・人格の見直し・Pulseの文面）が使う
        （core/chores/orchestrator.py）。DI済みのchat_call_fn(テスト用差し替え含む)をそのまま使う。"""
        return self._chat_call_fn(prompt)

    def choose_pulse(self, prompt: str) -> dict:
        """Pulseの1回の呼び出しで、話すかと文面を本人に選ばせる。失敗は見送りにせず例外にする。
        本人の言葉なので、返事と同じ温度で書く（温度0では、似た場面で毎回同じ言葉になる）。"""
        answer = self._answer(prompt, PULSE_CHOICE_SCHEMA, lambda _chunk: None)
        if answer is None:
            raise OllamaAdapterError("Pulseの選択を読み取れませんでした。")
        return answer

    def describe_motion(self, wish: str) -> dict:
        """本人の願いを動作生成器向けの短い英文にする。文言は記録・例外へ出さない。"""
        prompt = (
            "次の身振りを動作生成モデルのために英語で説明してください。"
            "JSONオブジェクトだけを返し、textはa personで始め、体のどこがどう動くかを"
            "英語の一文（200字以内）で具体的に書いてください。"
            "secondsは動作の長さを1〜10の整数秒で指定してください。\n"
            f"身振り: {wish}"
        )
        try:
            if self._uses_default_chat:
                payload = self._generate_payload(prompt, think=False, stream=True, num_predict=256)
                raw = self._stream({**payload, "format": MOTION_DESCRIBE_SCHEMA}, lambda _chunk: None)
            else:
                raw = self._chat_call_fn(prompt)
        except Exception:
            raise MotionDescribeUnavailable from None
        try:
            answer = self._extract_json(raw)
        except (OllamaAdapterError, ValueError):
            raise MotionDescribeInvalid from None
        if not isinstance(answer, dict):
            raise MotionDescribeInvalid
        return answer

    def warm(self) -> None:
        """脳を載せておく（替え玉の chat_call_fn のときは何もしない）。"""
        if self._uses_default_chat:
            serve.warm(f"{self._base_url}/api/generate", self._generate_payload("", think=False, stream=False))

    def converse(
        self,
        pack: ContextPack,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
        on_reply: Callable[[str], None] | None = None,
        catalog: BodyCatalog | None = None,
        on_body: Callable[[dict], None] | None = None,
    ) -> dict:
        """返答生成→（on_reply通知）→評価の順で1ターン分の報告書を作る。

        on_token/on_replyはGUIストリーミング用（2026-07-20）。on_replyは返答本文の確定直後・
        評価の前に呼ぶ。空返答のときは通知せず、評価も聞かない。
        catalog があれば評価の先頭で体の欄を聞き、閉じた時点で on_body へ脳の答えのまま渡す（確かめるのは Core）。
        """
        reply = self._chat_call_with_think(
            self.build_chat_prompt(pack), think=think, on_token=on_token,
        ).strip()
        if reply and on_reply is not None:
            on_reply(reply)
        return {
            "reply": reply,
            "appraisal": self.appraise(pack, reply, catalog=catalog, on_body=on_body) if reply else None,
        }

    def _chat_call_with_think(
        self,
        prompt: str,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        """返答生成用。第2・第3発注は常に think=False。

        DI差し替え（テスト・裏方のcall_fn）はthink/streamの概念を持たないため
        on_tokenは黙って無視される（一括応答）。呼び出し元はon_reply通知で吸収する。
        """
        if self._uses_default_chat:
            return self._default_chat_call(prompt, think=think, on_token=on_token)
        return self._chat_call_fn(prompt)

    def appraise(
        self,
        pack: ContextPack,
        reply: str,
        *,
        catalog: BodyCatalog | None = None,
        on_body: Callable[[dict], None] | None = None,
    ) -> dict | None:
        """返答のあとの評価（答えの形はJSON Schemaで縛る）。失敗時は None（会話を止めない）。

        答えは流しながら読み、先頭の体の欄がそろった時点で on_body へ渡す（替え玉の chat_call_fn は一括の答えから同じく取り出す）。
        答えの中身（選択肢にあるか・長さ）を確かめて整えるのは Core（評価は core/intake/gate.py、体の欄は core/perception.py）。
        """
        prompt = self.build_appraisal_prompt(pack, reply, catalog)
        return self._answer(prompt, appraisal_schema(catalog), _BodyFields(catalog, on_body), **STEADY)

    def choose_body(self, prompt: str, said: str, catalog: BodyCatalog) -> dict | None:
        """話しかけたあと（Pulse）、同じ前置きの後ろに言った言葉と体の欄だけの問いを足して聞く。失敗時は None。
        答えの中身を確かめるのは Core（core/perception.py）。"""
        body_prompt = f"{prompt}\n【あなたが今かけた言葉】\n{said}\n\n{body_alone_question(catalog)}\n"
        return self._answer(body_prompt, body_alone_schema(catalog), lambda _chunk: None, **STEADY)

    def _answer(self, prompt: str, schema: dict, heard: Callable[[str], None], **sampling: float) -> dict | None:
        """答えの形を JSON Schema で縛って聞く（温度は sampling。なければ返事と同じ）。流しながら heard へ渡し、
        答えの dict を返す（失敗時は None）。"""
        try:
            if self._uses_default_chat:
                payload = self._generate_payload(
                    prompt, think=False, stream=True, num_predict=APPRAISAL_MAX_TOKENS, **sampling,
                )
                text = self._stream({**payload, "format": schema}, heard)
            else:
                text = self._chat_call_fn(prompt)
                heard(text)
            answer = self._extract_json(text)
        except Exception:  # noqa: BLE001
            return None
        return answer if isinstance(answer, dict) else None

    def judge(self, prompt: str) -> dict:
        """persona非注入・think:false固定の判定発注(下ごしらえ)。呼び出し元は今回実装しない。

        think ON/OFF判定（§3.1改訂）とTavily検索要否判定が、persona非注入の
        neutral promptを渡してJSON応答を受け取る想定。呼び出しに使うcall_fn自体は
        converse/raw_callと共有する（think:falseは_default_chat_callに既定で載っている
        ため、呼び出し元ごとに個別設定する必要がない）。
        """
        response_text = self._chat_call_fn(prompt)
        return self._extract_json(response_text)

    @staticmethod
    def _extract_json(text: str) -> dict:
        """```json```フェンスからJSONを取り出す（フェンス無しなら全文を試す）。

        2026-07-26 A9: `core/chores/distillation.py:_extract_candidates`とほぼ同じ正規表現を
        持つが、意図的な重複であり共通化しない。片方はBrain通訳層（本ファイル）、
        片方は裏方便（distillation）と層が異なり、共通化すると層をまたぐ依存を作ってしまう。
        """
        match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
        candidate = match.group(1) if match else text
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise OllamaAdapterError(f"Ollama応答からJSONを抽出できない: {e}") from e

    def _generate_payload(self, prompt: str, *, think: bool, stream: bool, **options: float) -> dict:
        """Ollama /api/generate の本体。num_ctx/use_mmap は環境既定に任せず明示する
        （呼び出しごとに変えると、モデルの読み込み直しになる）。options は温度などの追加。"""
        return {
            "model": self._model,
            "prompt": prompt,
            "stream": stream,
            "think": think,
            "options": {"num_ctx": self._num_ctx, "use_mmap": self._use_mmap, **options},
        }

    def _default_chat_call(
        self,
        prompt: str,
        *,
        think: bool = False,
        on_token: Callable[[str], None] | None = None,
    ) -> str:
        if on_token is None:
            response = serve.post(
                f"{self._base_url}/api/generate",
                json=self._generate_payload(prompt, think=think, stream=False),
                timeout=self._request_timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            text = data.get("response")
            if not isinstance(text, str):
                raise OllamaAdapterError(f"Ollama応答にresponseが含まれない: {data}")
            return text  # 空も答え（Pulse で今は話さないと決めたときなど）

        text = self._stream(self._generate_payload(prompt, think=think, stream=True), on_token)
        if not text:
            raise OllamaAdapterError("Ollamaストリーム応答が空だった")
        return text

    def _stream(self, payload: dict, on_text: Callable[[str], None]) -> str:
        """stream:true の答えを、届いた分ずつ on_text へ渡しながら読み、全文を返す。

        NDJSON行の逐次到着。"response"は可視トークンのみで、think時の隠れ思考は"thinking"側に分離されるため、
        そのまま画面へ流してよい。timeoutはチャンク間の無応答ガードとして働く（総時間ではない）。
        """
        parts: list[str] = []
        with serve.post(
            f"{self._base_url}/api/generate",
            json=payload,
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
                        on_text(chunk)
                    except Exception:  # noqa: BLE001 — 表示側の失敗で生成を止めない
                        on_text = lambda _chunk: None  # noqa: E731
                if data.get("done"):
                    break
        return "".join(parts)
