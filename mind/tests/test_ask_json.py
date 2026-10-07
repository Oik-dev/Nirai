"""Ollama構造化出力の実リクエスト設定。"""

from __future__ import annotations

import mind.brains.ollama.ask_json as ask_json_module


def test_ask_json_sends_num_ctx_and_use_mmap(monkeypatch) -> None:  # noqa: ANN001
    captured: list[dict] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"response": "{}"}

    def fake_post(url, json=None, timeout=None):  # noqa: ANN001
        captured.append(json or {})
        return FakeResponse()

    monkeypatch.setattr(ask_json_module.serve, "post", fake_post)
    answer = ask_json_module.ask_json("test", num_ctx=2048, use_mmap=True)

    assert answer == {}
    assert captured[0]["options"]["num_ctx"] == 2048
    assert captured[0]["options"]["use_mmap"] is True
