"""ストリーミング表示（NDJSON 消費・on_token 伝搬）の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.chat_llm import OllamaChatConnector
from serina.skills.base import SkillContext
from serina.skills.chat import ChatSkill


def _ndjson(*objs) -> list[bytes]:
    return [json.dumps(o, ensure_ascii=False).encode("utf-8") for o in objs]


def test_consume_stream_assembles_and_notifies() -> None:
    lines = _ndjson(
        {"message": {"role": "assistant", "content": "おは"}, "done": False},
        {"message": {"role": "assistant", "content": "よう"}, "done": False},
        {"message": {"role": "assistant", "content": "！"}, "done": True},
    )
    received: list[str] = []
    full = OllamaChatConnector._consume_stream(lines, received.append)
    assert full == "おはよう！", f"全文組み立てが不正: {full}"
    assert received == ["おは", "よう", "！"], f"逐次通知が不正: {received}"


def test_consume_stream_skips_empty_and_stops_at_done() -> None:
    lines = [b""] + _ndjson(
        {"message": {"role": "assistant", "content": "A"}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": False},  # 空チャンクは通知しない
        {"message": {"role": "assistant", "content": "B"}, "done": True},
        {"message": {"role": "assistant", "content": "見えない残骸"}, "done": False},  # done後は読まない
    )
    received: list[str] = []
    full = OllamaChatConnector._consume_stream(lines, received.append)
    assert full == "AB", f"done 後を読んでいる: {full}"
    assert received == ["A", "B"], f"空チャンクを通知している: {received}"


def test_consume_stream_raises_on_error_and_empty() -> None:
    try:
        OllamaChatConnector._consume_stream(
            _ndjson({"error": "model not found"}), lambda _: None)
        raise AssertionError("error 行で例外が出ていない")
    except RuntimeError as e:
        assert "model not found" in str(e)

    try:
        OllamaChatConnector._consume_stream(
            _ndjson({"message": {"content": "  "}, "done": True}), lambda _: None)
        raise AssertionError("空応答で例外が出ていない")
    except RuntimeError:
        pass


class _RecordingConnector:
    """on_token の受け渡しを記録する ChatConnector フェイク"""

    def __init__(self) -> None:
        self.last_on_token = "unset"

    def chat(self, system, messages, options=None, on_token=None) -> str:
        self.last_on_token = on_token
        if on_token:
            on_token("hi")
        return "hi"


def test_chat_skill_forwards_on_token() -> None:
    conn = _RecordingConnector()
    skill = ChatSkill(conn)
    received: list[str] = []
    cb = received.append
    ctx = SkillContext("やあ", "sys", [], on_token=cb)
    assert skill.run(ctx) == "hi"
    assert conn.last_on_token is cb, "on_token が Connector に渡っていない"
    assert received == ["hi"]

    ctx2 = SkillContext("やあ", "sys", [])  # 省略時は None（後方互換）
    skill.run(ctx2)
    assert conn.last_on_token is None, "on_token 省略時に None になっていない"


def main() -> None:
    tests = [
        test_consume_stream_assembles_and_notifies,
        test_consume_stream_skips_empty_and_stops_at_done,
        test_consume_stream_raises_on_error_and_empty,
        test_chat_skill_forwards_on_token,
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
