"""Ollama通訳のテスト。設計書 §3.1・§2.2（返答のあとの評価）。

返答本文（reply）はJSON書式を強制しない単発呼びで得る。返答のあとに、本人の評価（今のやりとりをどう感じたか）を
聞く。問いは返答のときと同じ前置き（パック）の後ろに足すので、脳がその計算を使い回せる。答えの形はJSON Schemaで縛り、
失敗しても会話は止めない（appraisal=None）。答えの中身を確かめるのは Core の関所（tests/test_intake_gate.py）。
実際のOllama呼び出しはinjectableなchat_call_fnで差し替え、ネットワークに依存しない。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter, OllamaAdapterError, closed_fields
from mind.core.context.pack import build_context_pack
from mind.core.feeling.appraisal import appraisal_question, appraisal_schema
from mind.core.perception import BodyCatalog
from mind.core.state.session import SessionState

APPRAISAL = {"feeling": "労われて、ほっとした", "valence": "うれしい", "arousal": "少し動いた",
             "distance": "近づいた", "master_state": "疲れてそう"}
APPRAISAL_JSON = "```json\n" + json.dumps(APPRAISAL, ensure_ascii=False) + "\n```"


def _pack():
    return build_context_pack(
        persona_text="価値観: 誠実であること",
        absolute_rules="機微情報を漏らさない",
        session=SessionState(),
        master_utterance="今日は疲れたよ",
    )


class QueuedCallFn:
    """呼び出し順に決め打ちの応答を返す。何回目にどんなpromptが来たかも記録する。"""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.received_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        if not self._responses:
            raise AssertionError("想定より多く呼ばれた")
        return self._responses.pop(0)


def test_converse_sends_pack_render_verbatim_for_reply_call() -> None:
    """返答生成の1回目の呼び出しには、JSON書式強制の指示文を足さずpack.render()をそのまま渡す。"""
    call_fn = QueuedCallFn(["お疲れさま、ゆっくり休んでね", APPRAISAL_JSON])
    adapter = OllamaAdapter(chat_call_fn=call_fn)
    pack = _pack()

    adapter.converse(pack)

    assert call_fn.received_prompts[0] == pack.render(), "1回目の呼び出しはpack.render()以外を混入させてはいけない"


def test_converse_wraps_plain_text_reply_as_contract() -> None:
    """モデルの生テキストをそのままreplyに採用する。"""
    call_fn = QueuedCallFn(["お疲れさま、ゆっくり休んでね", APPRAISAL_JSON])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "お疲れさま、ゆっくり休んでね"


def test_converse_strips_surrounding_whitespace() -> None:
    call_fn = QueuedCallFn(["  返答本文  \n", APPRAISAL_JSON])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "返答本文"


def test_appraisal_is_asked_after_the_reply_on_the_same_prefix() -> None:
    """評価の問いは、返答のときと同じ前置き（パック全体）の後ろに、返した言葉と問いを足したもの。"""
    call_fn = QueuedCallFn(["お疲れさま", APPRAISAL_JSON])
    adapter = OllamaAdapter(chat_call_fn=call_fn)
    pack = _pack()

    result = adapter.converse(pack)

    second = call_fn.received_prompts[1]
    assert second.startswith(pack.render())
    assert "お疲れさま" in second[len(pack.render()):]
    assert second.rstrip().endswith(appraisal_question())
    assert result["appraisal"] == APPRAISAL  # 答えは脳のまま運ぶ（確かめるのは Core の関所）


def test_appraisal_failure_does_not_break_reply() -> None:
    """§2.4: 評価が壊れても会話は止めない。replyは無傷で appraisal だけ None になる。"""
    adapter = OllamaAdapter(chat_call_fn=QueuedCallFn(["ちゃんと届いた返答", "JSONではない自由文"]))
    result = adapter.converse(_pack())
    assert result["reply"] == "ちゃんと届いた返答"
    assert result["appraisal"] is None

    calls = {"n": 0}

    def call_fn(prompt: str) -> str:
        calls["n"] += 1
        if calls["n"] == 2:
            raise ConnectionError("接続エラー")
        return "無事届いた返答"

    result = OllamaAdapter(chat_call_fn=call_fn).converse(_pack())
    assert result["reply"] == "無事届いた返答"
    assert result["appraisal"] is None


def test_raw_call_delegates_directly_to_chat_call_fn() -> None:
    """裏方（会話の要約・人格の見直し）が使う素の呼び出し。評価は伴わない。"""
    received = []

    def call_fn(prompt: str) -> str:
        received.append(prompt)
        return "裏方の結果のテキスト"

    adapter = OllamaAdapter(chat_call_fn=call_fn)
    result = adapter.raw_call("何か発注プロンプト")

    assert result == "裏方の結果のテキスト"
    assert received == ["何か発注プロンプト"]


def test_judge_extracts_json_from_fenced_code_block() -> None:
    """think判定・検索要否判定が使う、persona非注入の判定呼び出し。"""
    adapter = OllamaAdapter(
        chat_call_fn=lambda prompt: '```json\n{"needs_deep_thinking": false, "reason": "日常会話"}\n```'
    )

    result = adapter.judge("何か判定プロンプト")

    assert result == {"needs_deep_thinking": False, "reason": "日常会話"}


def test_judge_raises_on_malformed_json() -> None:
    adapter = OllamaAdapter(chat_call_fn=lambda prompt: "JSONではない自由文")

    try:
        adapter.judge("何か判定プロンプト")
    except OllamaAdapterError:
        pass
    else:
        raise AssertionError("不正なJSON応答はOllamaAdapterErrorであるべき")


def test_converse_fires_on_reply_before_the_appraisal() -> None:
    """2026-07-20 応答高速化: on_reply（本文確定通知）は評価より前に発火する
    ＝GUIに返答が見えてから評価が裏で走る。"""
    order: list[str] = []

    def call_fn(prompt: str) -> str:
        order.append("call")
        if len(order) == 1:
            return "返答本文"
        return APPRAISAL_JSON

    adapter = OllamaAdapter(chat_call_fn=call_fn)
    result = adapter.converse(
        _pack(),
        on_reply=lambda reply: order.append(f"on_reply:{reply}"),
    )

    assert result["reply"] == "返答本文"
    assert order[0] == "call"
    assert order[1] == "on_reply:返答本文", "on_reply は評価の前に発火すべき"
    assert order[2:] == ["call"]


def test_converse_skips_on_reply_and_appraisal_for_empty_reply() -> None:
    """空返答（契約違反→最終防衛線行き）は on_reply を発火せず、評価も聞かない（空吹き出し防止）。"""
    fired: list[str] = []
    call_fn = QueuedCallFn(["   "])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack(), on_reply=fired.append)

    assert result["reply"] == ""
    assert result["appraisal"] is None
    assert fired == []
    assert len(call_fn.received_prompts) == 1


def test_default_chat_call_streams_tokens(monkeypatch) -> None:  # noqa: ANN001
    """on_token 指定時は stream:true の NDJSON を逐次読み、可視トークンだけを流す。"""
    captured_payload: list[dict] = []

    class FakeStreamResponse:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):  # noqa: ANN202
            yield b'{"response": "\\u304a\\u75b2\\u308c", "done": false}'
            yield b""
            yield b'{"response": "\\u3055\\u307e", "done": false}'
            yield b'{"response": "", "done": true}'

    def fake_post(url, json=None, timeout=None, stream=False):  # noqa: ANN001
        captured_payload.append(dict(json or {}, _stream_kwarg=stream))
        return FakeStreamResponse()

    import mind.brains.ollama.adapter as adapter_module

    monkeypatch.setattr(adapter_module.serve, "post", fake_post)
    adapter = OllamaAdapter()
    tokens: list[str] = []

    text = adapter._default_chat_call("プロンプト", on_token=tokens.append)

    assert text == "お疲れさま"
    assert tokens == ["お疲れ", "さま"]
    assert captured_payload[0]["stream"] is True
    assert captured_payload[0]["_stream_kwarg"] is True
    assert captured_payload[0]["options"]["num_ctx"] == 8192
    assert captured_payload[0]["options"]["use_mmap"] is True


def test_compose_advisor_followup_removed() -> None:
    """Phase E: 2通目生成機構（compose_advisor_followup）は退役済み。属性自体が無い。"""
    adapter = OllamaAdapter(chat_call_fn=lambda p: "x")
    assert not hasattr(adapter, "compose_advisor_followup")
    assert not hasattr(adapter, "build_advisor_followup_prompt")


def _capture_posts(monkeypatch, response_text: str, *, chunks: list[str] | None = None, heard: list | None = None) -> list[dict]:  # noqa: ANN001
    """Ollama の替え玉。一括の答えは response_text、流す答え（評価）は chunks（なければ response_text を1つ）で返す。
    heard を渡すと、流した断片を順に積む（体の欄がどこまで届いた時点で渡されたかを見るため）。"""
    captured: list[dict] = []

    class FakeResponse:
        def __enter__(self):  # noqa: ANN204
            return self

        def __exit__(self, *args):  # noqa: ANN002, ANN204
            return None

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": response_text}

        def iter_lines(self):  # noqa: ANN202
            for chunk in chunks or [response_text]:
                if heard is not None:
                    heard.append(chunk)
                yield json.dumps({"response": chunk, "done": False}).encode()
            yield b'{"response": "", "done": true}'

    def fake_post(url, json=None, timeout=None, stream=False):  # noqa: ANN001
        captured.append(json or {})
        return FakeResponse()

    import mind.brains.ollama.adapter as adapter_module

    monkeypatch.setattr(adapter_module.serve, "post", fake_post)
    return captured


def test_converse_passes_think_flag_to_api_payload(monkeypatch) -> None:  # noqa: ANN001
    """think ON/OFF が Ollama generate の JSON に載る。評価はいつも think:false。"""
    captured = _capture_posts(monkeypatch, "返答")
    adapter = OllamaAdapter()

    adapter.converse(_pack(), think=False)
    adapter.converse(_pack(), think=True)

    assert captured[0]["think"] is False
    assert captured[1]["think"] is False, "評価"
    assert captured[2]["think"] is True
    assert captured[3]["think"] is False, "評価は think:false 維持"


def test_appraisal_request_is_shaped_by_the_schema_on_the_same_model_settings(monkeypatch) -> None:  # noqa: ANN001
    """評価は答えの形を JSON Schema で縛り、温度0で聞く。num_ctx・use_mmap は返答と同じ（変えると読み込み直しになる）。"""
    captured = _capture_posts(monkeypatch, json.dumps(APPRAISAL, ensure_ascii=False))
    adapter = OllamaAdapter(num_ctx=4096, use_mmap=True)

    result = adapter.converse(_pack())

    reply_call, appraisal_call = captured
    assert appraisal_call["format"] == appraisal_schema()
    assert appraisal_call["options"]["temperature"] == 0.0
    assert appraisal_call["options"]["num_ctx"] == reply_call["options"]["num_ctx"] == 4096
    assert appraisal_call["options"]["use_mmap"] == reply_call["options"]["use_mmap"] is True
    assert appraisal_call["prompt"].startswith(reply_call["prompt"])
    assert result["appraisal"] == APPRAISAL


CATALOG = BodyCatalog(expressions=("喜び", "驚き"), gestures=("うなずく",))


def test_closed_fields_takes_only_the_fields_that_have_closed() -> None:
    """届いた途中の文字から、閉じた欄だけ。書いている途中の欄・文字の中の区切り・壊れた文字には惑わされない。"""
    assert closed_fields('{"expression": "喜び", "gest') == {"expression": "喜び"}
    assert closed_fields('{"expression": "喜び", "gesture": "うなずく", "feeling": "うれ') == {
        "expression": "喜び", "gesture": "うなずく"}
    assert closed_fields('{"feeling": "a,b}\\"c", "x": [1, {"y": 2}], "z') == {"feeling": 'a,b}"c', "x": [1, {"y": 2}]}
    assert closed_fields('```json\n{"a": "b"}\n```') == {"a": "b"}
    assert closed_fields('{"expression": "喜') == {}
    assert closed_fields("JSONではない自由文") == {}
    assert closed_fields('{"a": tru, "b": 1,') == {}


def test_body_fields_lead_the_question_and_reach_on_body_before_the_appraisal_ends(monkeypatch) -> None:  # noqa: ANN001
    """体の欄は問いと答えの先頭。流しながら読み、そろった時点で（評価の残りが届く前に）脳の答えのまま渡す。"""
    answer = {"expression": "喜び", "gesture": "うなずく", "wish": "", **APPRAISAL}
    text = json.dumps(answer, ensure_ascii=False)
    cut = text.index('"feeling"')
    heard: list[str] = []
    captured = _capture_posts(monkeypatch, "返答", chunks=[text[:20], text[20:cut], text[cut:]], heard=heard)
    bodies: list[tuple[dict, int]] = []

    result = OllamaAdapter().converse(_pack(), catalog=CATALOG, on_body=lambda fields: bodies.append((fields, len(heard))))

    _, appraisal_call = captured
    assert appraisal_call["stream"] is True
    assert list(appraisal_call["format"]["properties"])[:4] == ["expression", "gesture", "wish", "feeling"]
    assert appraisal_call["format"]["properties"]["expression"]["enum"] == ["喜び", "驚き", "そのまま", "なし"]
    assert appraisal_call["format"] == appraisal_schema(CATALOG)
    assert appraisal_call["prompt"].rstrip().endswith(appraisal_question(CATALOG))
    assert "喜び / 驚き / そのまま / なし" in appraisal_question(CATALOG)
    assert bodies == [({"expression": "喜び", "gesture": "うなずく", "wish": ""}, 2)], "評価の残り（3つ目の断片）を読む前に、1回だけ"
    assert result["appraisal"] == answer  # 評価は今までどおり（体の欄があっても）


def test_the_body_comes_from_the_whole_answer_of_a_stand_in_brain() -> None:
    """替え玉の脳（一括の答え）でも、同じ取り出しで体の欄を渡す。"""
    answer = "```json\n" + json.dumps({"expression": "驚き", "gesture": "なし", "wish": "", **APPRAISAL}, ensure_ascii=False) + "\n```"
    call_fn = QueuedCallFn(["返答", answer])
    bodies: list[dict] = []

    result = OllamaAdapter(chat_call_fn=call_fn).converse(_pack(), catalog=CATALOG, on_body=bodies.append)

    assert bodies == [{"expression": "驚き", "gesture": "なし", "wish": ""}]
    assert result["appraisal"]["feeling"] == APPRAISAL["feeling"]
    assert "expression: " in call_fn.received_prompts[1]


def test_a_failing_body_leaves_the_appraisal_as_it_was() -> None:
    """体の欄で失敗しても（渡した先が落ちても・欄が壊れていても）、評価は変わらない。"""
    answer = json.dumps({"expression": "喜び", "gesture": "うなずく", **APPRAISAL}, ensure_ascii=False)

    def broken(_fields: dict) -> None:
        raise RuntimeError("窓へ流せなかった")

    result = OllamaAdapter(chat_call_fn=QueuedCallFn(["返答", answer])).converse(_pack(), catalog=CATALOG, on_body=broken)
    assert result["appraisal"]["feeling"] == APPRAISAL["feeling"]

    bodies: list[dict] = []
    torn = '{"expression": "喜び", "gesture": "うな'
    result = OllamaAdapter(chat_call_fn=QueuedCallFn(["返答", torn])).converse(_pack(), catalog=CATALOG, on_body=bodies.append)
    assert bodies == [] and result["appraisal"] is None


def test_without_a_catalog_the_question_is_the_appraisal_alone(monkeypatch) -> None:  # noqa: ANN001
    """カタログがなければ体の欄は聞かず、問いも答えの形も評価だけ。"""
    captured = _capture_posts(monkeypatch, json.dumps(APPRAISAL, ensure_ascii=False))
    bodies: list[dict] = []

    OllamaAdapter().converse(_pack(), on_body=bodies.append)

    assert list(captured[1]["format"]["properties"]) == ["feeling", "valence", "arousal", "distance", "master_state"]
    assert "expression" not in captured[1]["prompt"]
    assert bodies == []
