import asyncio
import json
from pathlib import Path

import pytest

from core.brains.base import BrainResponseError, BrainUnavailableError
from core.brains.claude_code import ClaudeCodeDriver
from core.brains.process_manager import CompletedInvocation


class FakeProcessManager:
    def __init__(self, outputs: list[CompletedInvocation]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    async def run(
        self,
        invocation_id: str,
        argv,
        *,
        cwd: Path,
        timeout_sec: float,
        stdin_text: str | None = None,
    ) -> CompletedInvocation:
        self.calls.append(
            {
                "invocation_id": invocation_id,
                "argv": tuple(argv),
                "cwd": cwd,
                "timeout_sec": timeout_sec,
                "stdin_text": stdin_text,
            }
        )
        return self.outputs.pop(0)

    async def cancel(self, invocation_id: str) -> bool:
        self.cancelled.append(invocation_id)
        return True


def _success_envelope(say: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "result": json.dumps({"say": say, "actions": [], "pass": False}, ensure_ascii=False),
    }, ensure_ascii=False)


def test_claude_driver_uses_noninteractive_safe_no_tools_mode_and_parses_result(tmp_path: Path) -> None:
    fake = FakeProcessManager([CompletedInvocation(0, _success_envelope("こんにちは"), "")])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    response = asyncio.run(driver.think(
        "INV-CLAUDE-1",
        "talk",
        {"name": "Shiro", "persona": "静かに話す。", "brain_model": "sonnet"},
        {"history": [{"from": "master", "text": "こんにちは"}]},
    ))

    assert response.say == "こんにちは"
    assert response.actions == ()
    assert response.passed is False
    assert len(fake.calls) == 1

    call = fake.calls[0]
    argv = call["argv"]
    assert isinstance(argv, tuple)
    assert argv[0] == "claude.exe"
    assert "-p" in argv
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--safe-mode" in argv
    assert "--tools" in argv
    tools_index = argv.index("--tools")
    assert argv[tools_index + 1] == ""
    assert "--permission-mode" in argv
    assert "dontAsk" in argv
    assert "--no-session-persistence" in argv
    assert "--output-format" in argv
    assert "json" in argv
    assert "--json-schema" in argv
    assert call["cwd"] == tmp_path / "runtime" / "brain_workspace"
    prompt = call["stdin_text"]
    assert isinstance(prompt, str)
    assert "Master: こんにちは" in prompt
    assert "静かに話す。" in prompt


def test_claude_driver_accepts_structured_output_envelope(tmp_path: Path) -> None:
    raw = json.dumps({
        "type": "result",
        "structured_output": {"say": "構造化済み", "actions": [], "pass": False},
    }, ensure_ascii=False)
    fake = FakeProcessManager([CompletedInvocation(0, raw, "")])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    response = asyncio.run(driver.think("INV-STRUCTURED", "talk", {"name": "Shiro"}, {"history": []}))

    assert response.say == "構造化済み"


def test_claude_driver_supports_private_whisper_context(tmp_path: Path) -> None:
    fake = FakeProcessManager([CompletedInvocation(0, _success_envelope("内緒にするね"), "")])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    response = asyncio.run(driver.think(
        "INV-CLAUDE-WHISPER",
        "whisper",
        {"name": "Shiro", "persona": "穏やかに話す。"},
        {
            "private_context": "前回の秘密",
            "recent_whispers": [{"from": "master", "to": "Shiro", "text": "内緒だよ"}],
            "current_whisper_history": [{"from": "master", "to": "Shiro", "text": "今日の秘密"}],
            "public_history": [{"from": "master", "text": "公開の話"}],
        },
    ))

    assert response.say == "内緒にするね"
    prompt = fake.calls[0]["stdin_text"]
    assert isinstance(prompt, str)
    assert "1対1のWhisper" in prompt
    assert "前回の秘密" in prompt
    assert "今日の秘密" in prompt
    assert "公開の話" in prompt


def test_claude_driver_retries_invalid_json_once(tmp_path: Path) -> None:
    fake = FakeProcessManager([
        CompletedInvocation(0, json.dumps({"type": "result", "result": "not-json"}), ""),
        CompletedInvocation(0, _success_envelope("再試行成功"), ""),
    ])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    response = asyncio.run(driver.think("INV-RETRY", "talk", {"name": "Shiro"}, {"history": []}))

    assert response.say == "再試行成功"
    assert len(fake.calls) == 2


def test_claude_driver_raises_after_second_invalid_response(tmp_path: Path) -> None:
    fake = FakeProcessManager([
        CompletedInvocation(0, "bad", ""),
        CompletedInvocation(0, "still-bad", ""),
    ])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    with pytest.raises(BrainResponseError):
        asyncio.run(driver.think("INV-BAD", "talk", {"name": "Shiro"}, {"history": []}))


def test_claude_driver_classifies_subscription_403_as_unavailable(tmp_path: Path) -> None:
    raw = json.dumps({
        "is_error": True,
        "api_error_status": 403,
        "result": "Your organization has disabled Claude subscription access for Claude Code",
        "type": "result",
    })
    fake = FakeProcessManager([CompletedInvocation(1, raw, "")])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    with pytest.raises(BrainUnavailableError, match="subscription access"):
        asyncio.run(driver.think("INV-403", "talk", {"name": "Shiro"}, {"history": []}))

    assert len(fake.calls) == 1


def test_claude_driver_cancel_targets_same_invocation(tmp_path: Path) -> None:
    fake = FakeProcessManager([])
    driver = ClaudeCodeDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("claude.exe",),
    )

    assert asyncio.run(driver.cancel("INV-CANCEL")) is True
    assert fake.cancelled == ["INV-CANCEL"]
