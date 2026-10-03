"""Ollama通訳のテスト。合意台帳 §9・§6-1・§7.1、2026-07-18決定7（感情付箋の第2発注復元）。

返答本文（reply）はJSON書式を強制しない単発呼びで得る。感情の動き（心の動き・マスター観測
付箋）はEmotionState/RelationshipStateの唯一の更新経路（core/intake/gate.py）であるため、
persona非注入・think:falseの軽量な第2発注で別途抽出する（失敗しても会話は止めない）。
実際のOllama呼び出しはinjectableなchat_call_fnで差し替え、ネットワークに依存しない。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter, OllamaAdapterError
from mind.core.context.pack import build_context_pack
from mind.core.state.session import SessionState


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
    call_fn = QueuedCallFn([
        "お疲れさま、ゆっくり休んでね",
        "```json\n{\"fusen_list\": []}\n```",
    ])
    adapter = OllamaAdapter(chat_call_fn=call_fn)
    pack = _pack()

    adapter.converse(pack)

    assert call_fn.received_prompts[0] == pack.render(), "1回目の呼び出しはpack.render()以外を混入させてはいけない"


def test_converse_wraps_plain_text_reply_as_contract() -> None:
    """モデルの生テキストをそのままreplyに採用する。"""
    call_fn = QueuedCallFn([
        "お疲れさま、ゆっくり休んでね",
        "```json\n{\"fusen_list\": []}\n```",
    ])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "お疲れさま、ゆっくり休んでね"
    assert result["self_assessment"]["over_capacity"] is False
    assert result["self_assessment"]["reason"]


def test_converse_strips_surrounding_whitespace() -> None:
    call_fn = QueuedCallFn([
        "  返答本文  \n",
        "```json\n{\"fusen_list\": []}\n```",
    ])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "返答本文"


def test_converse_extracts_emotion_fusen_from_second_call() -> None:
    """§2.3/§2.6: EmotionState/RelationshipStateの唯一の更新経路。第2発注のJSONから
    心の動き・マスター観測付箋を拾い上げてfusen_listへ載せる。"""
    extraction_json = (
        '```json\n{"fusen_list": ['
        '{"kind": "心の動き", "version": 1, '
        '"content": {"deltas": {"喜び": 0.1}, "きっかけ": "労われた"}, "confidence": 0.8}, '
        '{"kind": "マスター観測", "version": 1, '
        '"content": {"observation": "疲れてそう"}, "confidence": 0.8}'
        ']}\n```'
    )
    call_fn = QueuedCallFn(["お疲れさま", extraction_json])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    kinds = {f["kind"] for f in result["fusen_list"]}
    assert kinds == {"心の動き", "マスター観測"}


def test_converse_drops_unknown_emotion_axis_keys() -> None:
    """§5.5-7・completion-review指摘: 幻覚キーはCore側のKeyErrorクラッシュを防ぐため
    adapter内部で黙って落とす（既知のプルチック8軸のみ通す）。"""
    extraction_json = (
        '```json\n{"fusen_list": ['
        '{"kind": "心の動き", "version": 1, '
        '"content": {"deltas": {"喜び": 0.5, "幸福": 0.3, "happiness": 0.2}, "きっかけ": "x"}, '
        '"confidence": 0.8}'
        ']}\n```'
    )
    call_fn = QueuedCallFn(["お疲れさま", extraction_json])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    deltas = result["fusen_list"][0]["content"]["deltas"]
    assert deltas == {"喜び": 0.5}, "未知の軸名（幻覚キー）は落とし、既知の軸だけ残すべき"


def test_converse_drops_non_numeric_delta_values() -> None:
    extraction_json = (
        '```json\n{"fusen_list": ['
        '{"kind": "心の動き", "version": 1, '
        '"content": {"deltas": {"喜び": "たくさん"}, "きっかけ": "x"}, "confidence": 0.8}'
        ']}\n```'
    )
    call_fn = QueuedCallFn(["お疲れさま", extraction_json])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["fusen_list"][0]["content"]["deltas"] == {}, "数値でないdelta値は落とすべき"


def test_converse_second_call_receives_utterance_and_reply_not_full_persona() -> None:
    """第2発注はpersona非注入（pack.render()全文ではなく発言と返答のペアのみを渡す）。"""
    call_fn = QueuedCallFn([
        "お疲れさま",
        "```json\n{\"fusen_list\": []}\n```",
    ])
    adapter = OllamaAdapter(chat_call_fn=call_fn)
    pack = _pack()

    adapter.converse(pack)

    second_prompt = call_fn.received_prompts[1]
    assert pack.persona_text not in second_prompt
    assert pack.master_utterance in second_prompt
    assert "お疲れさま" in second_prompt


def test_converse_emotion_extraction_failure_does_not_break_reply() -> None:
    """§2.4: 裏方（感情抽出）が壊れても会話は止めない。replyは無傷でfusen_listだけ空になる。"""
    call_fn = QueuedCallFn(["ちゃんと届いた返答", "JSONではない自由文"])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "ちゃんと届いた返答"
    assert result["fusen_list"] == []


def test_converse_emotion_extraction_call_raising_does_not_break_reply() -> None:
    call_count = {"n": 0}

    def call_fn(prompt: str) -> str:
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise ConnectionError("接続エラー")
        return "無事届いた返答"

    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack())

    assert result["reply"] == "無事届いた返答"
    assert result["fusen_list"] == []


def test_raw_call_delegates_directly_to_chat_call_fn() -> None:
    """裏方（会話の要約・人格の見直し）が使う素の呼び出し。第2発注は伴わない。"""
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


def test_converse_fires_on_reply_before_extraction_calls() -> None:
    """2026-07-20 応答高速化: on_reply（本文確定通知）は感情報告より前に発火する
    ＝GUIに返答が見えてから抽出が裏で走る。"""
    order: list[str] = []

    def call_fn(prompt: str) -> str:
        order.append("call")
        if len(order) == 1:
            return "返答本文"
        return '```json\n{"fusen_list": []}\n```'

    adapter = OllamaAdapter(chat_call_fn=call_fn)
    result = adapter.converse(
        _pack(),
        on_reply=lambda reply: order.append(f"on_reply:{reply}"),
    )

    assert result["reply"] == "返答本文"
    assert order[0] == "call"
    assert order[1] == "on_reply:返答本文", "on_reply は抽出発注の前に発火すべき"
    assert order[2:] == ["call"]


def test_converse_skips_on_reply_for_empty_reply() -> None:
    """空返答（契約違反→最終防衛線行き）は on_reply を発火しない（空吹き出し防止）。"""
    fired: list[str] = []
    call_fn = QueuedCallFn(["   ", "```json\n{\"fusen_list\": []}\n```"])
    adapter = OllamaAdapter(chat_call_fn=call_fn)

    result = adapter.converse(_pack(), on_reply=fired.append)

    assert result["reply"] == ""
    assert fired == []


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

    monkeypatch.setattr(adapter_module.requests, "post", fake_post)
    adapter = OllamaAdapter()
    tokens: list[str] = []

    text = adapter._default_chat_call("プロンプト", on_token=tokens.append)

    assert text == "お疲れさま"
    assert tokens == ["お疲れ", "さま"]
    assert captured_payload[0]["stream"] is True
    assert captured_payload[0]["_stream_kwarg"] is True
    assert captured_payload[0]["options"]["num_ctx"] == 8192


def test_compose_advisor_followup_removed() -> None:
    """Phase E: 2通目生成機構（compose_advisor_followup）は退役済み。属性自体が無い。"""
    adapter = OllamaAdapter(chat_call_fn=lambda p: "x")
    assert not hasattr(adapter, "compose_advisor_followup")
    assert not hasattr(adapter, "build_advisor_followup_prompt")


def test_converse_passes_think_flag_to_api_payload(monkeypatch) -> None:  # noqa: ANN001
    """think ON/OFF が Ollama generate の JSON に載る（requests をモック）。"""
    captured: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "返答"}

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured.append(json or {})
        return FakeResponse()

    import mind.brains.ollama.adapter as adapter_module

    monkeypatch.setattr(adapter_module.requests, "post", fake_post)
    adapter = OllamaAdapter()

    adapter.converse(_pack(), think=False)
    adapter.converse(_pack(), think=True)

    assert captured[0]["think"] is False
    assert captured[1]["think"] is False, "感情報告"
    assert captured[2]["think"] is True
    assert captured[3]["think"] is False, "感情報告"


def test_emotion_second_call_always_uses_think_false(monkeypatch) -> None:  # noqa: ANN001
    captured: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "x"}

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured.append(json or {})
        return FakeResponse()

    import mind.brains.ollama.adapter as adapter_module

    monkeypatch.setattr(adapter_module.requests, "post", fake_post)
    adapter = OllamaAdapter()
    adapter.converse(_pack(), think=True)

    assert captured[0]["think"] is True
    assert captured[1]["think"] is False, "感情報告は think:false 維持"


def main() -> None:
    tests = [
        test_converse_sends_pack_render_verbatim_for_reply_call,
        test_converse_wraps_plain_text_reply_as_contract,
        test_converse_strips_surrounding_whitespace,
        test_converse_extracts_emotion_fusen_from_second_call,
        test_converse_drops_unknown_emotion_axis_keys,
        test_converse_drops_non_numeric_delta_values,
        test_converse_second_call_receives_utterance_and_reply_not_full_persona,
        test_converse_emotion_extraction_failure_does_not_break_reply,
        test_converse_emotion_extraction_call_raising_does_not_break_reply,
        test_raw_call_delegates_directly_to_chat_call_fn,
        test_judge_extracts_json_from_fenced_code_block,
        test_judge_raises_on_malformed_json,
        test_converse_fires_on_reply_before_extraction_calls,
        test_converse_skips_on_reply_for_empty_reply,
        test_compose_advisor_followup_returns_second_message,
        test_compose_advisor_followup_failure_returns_empty,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
