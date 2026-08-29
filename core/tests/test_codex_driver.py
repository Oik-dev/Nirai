import asyncio
from pathlib import Path

import pytest

from core.brains.base import BrainError, BrainResponseError
from core.brains.codex import CodexDriver
from core.brains.process_manager import CompletedInvocation, decode_process_output


def test_process_output_decoder_accepts_utf8_and_windows_cp932() -> None:
    text = "こんにちは、Master。"
    assert decode_process_output(text.encode("utf-8")) == text
    assert decode_process_output(text.encode("cp932")) == text


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


def test_codex_driver_uses_ephemeral_read_only_exec_and_parses_response(tmp_path: Path) -> None:
    fake = FakeProcessManager(
        [CompletedInvocation(0, '{"say":"こんにちは","actions":[],"pass":false}', "")]
    )
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    response = asyncio.run(
        driver.think(
            "INV-1",
            "talk",
            {"name": "Resident", "persona": "静かに話す。"},
            {
                "history": [
                    {"from": "master", "text": "こんにちは"},
                ]
            },
        )
    )

    assert response.say == "こんにちは"
    assert response.actions == ()
    assert response.passed is False
    assert len(fake.calls) == 1

    call = fake.calls[0]
    argv = call["argv"]
    assert isinstance(argv, tuple)
    assert argv[:2] == ("node.exe", "codex.js")
    assert "exec" in argv
    assert "--ephemeral" in argv
    assert "read-only" in argv
    assert "--output-schema" in argv
    assert argv[-1] == "-"
    assert call["cwd"] == tmp_path / "runtime" / "brain_workspace"
    prompt = call["stdin_text"]
    assert isinstance(prompt, str)
    assert "Master: こんにちは" in prompt
    assert "静かに話す。" in prompt


def test_codex_driver_supports_private_whisper_context(tmp_path: Path) -> None:
    fake = FakeProcessManager(
        [CompletedInvocation(0, '{"say":"内緒にするね","actions":[],"pass":false}', "")]
    )
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    response = asyncio.run(driver.think(
        "INV-WHISPER",
        "whisper",
        {"name": "Lapan", "persona": "静かに話す。"},
        {
            "private_context": "前回の秘密",
            "recent_whispers": [{"from": "master", "to": "Lapan", "text": "内緒だよ"}],
            "current_whisper_history": [{"from": "master", "to": "Lapan", "text": "今日の秘密"}],
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


def test_codex_driver_retries_invalid_json_once(tmp_path: Path) -> None:
    fake = FakeProcessManager(
        [
            CompletedInvocation(0, "not-json", ""),
            CompletedInvocation(0, '{"say":"再試行成功","actions":[],"pass":false}', ""),
        ]
    )
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    response = asyncio.run(
        driver.think("INV-2", "talk", {"name": "Resident"}, {"history": []})
    )

    assert response.say == "再試行成功"
    assert [call["invocation_id"] for call in fake.calls] == ["INV-2", "INV-2"]


def test_codex_driver_raises_after_second_invalid_response(tmp_path: Path) -> None:
    fake = FakeProcessManager(
        [
            CompletedInvocation(0, "bad", ""),
            CompletedInvocation(0, "still-bad", ""),
        ]
    )
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    with pytest.raises(BrainResponseError):
        asyncio.run(driver.think("INV-3", "talk", {"name": "Resident"}, {"history": []}))


def test_codex_driver_cancel_targets_the_same_invocation_id(tmp_path: Path) -> None:
    fake = FakeProcessManager([])
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    cancelled = asyncio.run(driver.cancel("INV-CANCEL"))

    assert cancelled is True
    assert fake.cancelled == ["INV-CANCEL"]


def test_codex_driver_does_not_retry_cli_failure(tmp_path: Path) -> None:
    fake = FakeProcessManager([CompletedInvocation(1, "", "authentication failed")])
    driver = CodexDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "codex.js"),
    )

    with pytest.raises(BrainError, match="authentication failed"):
        asyncio.run(driver.think("INV-4", "talk", {"name": "Resident"}, {"history": []}))

    assert len(fake.calls) == 1
