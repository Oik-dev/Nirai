import asyncio
import json
from pathlib import Path

import pytest

from core.brains import cursor as cursor_module
from core.brains import process_manager as process_manager_module
from core.brains.base import BrainResponseError
from core.brains.cursor import CursorDriver
from core.brains.process_manager import CompletedInvocation, ProcessManager


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
        env=None,
    ) -> CompletedInvocation:
        self.calls.append({
            "invocation_id": invocation_id,
            "argv": tuple(argv),
            "cwd": cwd,
            "timeout_sec": timeout_sec,
            "stdin_text": stdin_text,
            "env": env,
        })
        return self.outputs.pop(0)

    async def cancel(self, invocation_id: str) -> bool:
        self.cancelled.append(invocation_id)
        return True


def _success(say: str) -> str:
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": json.dumps({"say": say, "actions": [], "pass": False}, ensure_ascii=False),
    }, ensure_ascii=False)


def test_process_manager_hides_windows_console_for_brain_invocations(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pid = 4321
        returncode: int | None = None

        async def communicate(self, stdin: bytes | None = None):
            captured["stdin"] = stdin
            self.returncode = 0
            return b"ok", b""

    async def fake_create_subprocess_exec(*argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(
        process_manager_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    result = asyncio.run(ProcessManager().run(
        "INV-HIDDEN",
        ("node.exe", "cursor-index.js"),
        cwd=tmp_path,
        timeout_sec=5,
        stdin_text="hello",
        env={"NIRAI_TEST_ENV": "1"},
    ))

    assert result.returncode == 0
    assert result.stdout == "ok"
    assert captured["creationflags"] == process_manager_module._windows_subprocess_flags()
    assert captured["env"] == {"NIRAI_TEST_ENV": "1"}


def test_cursor_model_catalog_restores_reasoning_label_when_cli_omits_high(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(argv, **kwargs):
        return type("Completed", (), {
            "returncode": 0,
            "stdout": (
                "cursor-grok-4.6-high - Cursor Grok 4.6\n"
                "cursor-grok-4.6-high-fast - Cursor Grok 4.6 Fast\n"
                "cursor-grok-4.6-xhigh - Cursor Grok 4.6 Extra High\n"
            ),
            "stderr": "",
        })()

    monkeypatch.setattr(cursor_module, "resolve_cursor_command", lambda: ("node.exe", "cursor-index.js"))
    monkeypatch.setattr(cursor_module.subprocess, "run", fake_run)

    assert cursor_module.list_cursor_models() == [
        {"id": "cursor-grok-4.6-high", "display_name": "Cursor Grok 4.6 High"},
        {"id": "cursor-grok-4.6-high-fast", "display_name": "Cursor Grok 4.6 High Fast"},
        {"id": "cursor-grok-4.6-xhigh", "display_name": "Cursor Grok 4.6 Extra High"},
    ]


def test_cursor_model_catalog_hides_windows_console_and_uses_isolated_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return type("Completed", (), {
            "returncode": 0,
            "stdout": "cursor-grok-4.6-high - Grok 4.6 High\n",
            "stderr": "",
        })()

    monkeypatch.setattr(cursor_module, "resolve_cursor_command", lambda: ("node.exe", "cursor-index.js"))
    monkeypatch.setattr(cursor_module.subprocess, "run", fake_run)

    models = cursor_module.list_cursor_models(tmp_path)

    assert models == [{"id": "cursor-grok-4.6-high", "display_name": "Grok 4.6 High"}]
    assert captured["creationflags"] == cursor_module._windows_subprocess_flags()
    env = captured["env"]
    assert isinstance(env, dict)
    profile_root = tmp_path / "runtime" / "cursor_profile"
    assert env["USERPROFILE"] == str(profile_root)
    assert env["HOME"] == str(profile_root)
    assert env["CURSOR_CONFIG_DIR"] == str(profile_root / ".cursor")


def test_cursor_driver_uses_read_only_ask_mode_empty_workspace_and_stdin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = FakeProcessManager([CompletedInvocation(0, _success("こんにちは"), "")])
    isolated_workspace = tmp_path / "local-app-data" / "Nirai" / "cursor_brain_workspace"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    response = asyncio.run(driver.think(
        "INV-CURSOR-1",
        "talk",
        {"name": "Kina", "persona": "短く話す。", "brain_model": "cursor-grok-4.6-high"},
        {"history": [{"from": "master", "text": "こんにちは"}]},
    ))

    assert response.say == "こんにちは"
    assert response.actions == ()
    assert response.passed is False
    call = fake.calls[0]
    argv = call["argv"]
    assert isinstance(argv, tuple)
    assert argv[:2] == ("node.exe", "cursor-index.js")
    assert "-p" in argv
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-high"
    assert "--mode" in argv
    assert argv[argv.index("--mode") + 1] == "ask"
    assert "--trust" in argv
    assert "--output-format" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--workspace" in argv
    assert argv[argv.index("--workspace") + 1] == str(isolated_workspace)
    assert "こんにちは" not in " ".join(argv)
    assert call["stdin_text"] is not None
    assert "Master: こんにちは" in str(call["stdin_text"])
    assert call["cwd"] == isolated_workspace
    env = call["env"]
    assert isinstance(env, dict)
    profile_root = tmp_path / "runtime" / "cursor_profile"
    assert env["USERPROFILE"] == str(profile_root)
    assert env["HOME"] == str(profile_root)
    assert env["CURSOR_CONFIG_DIR"] == str(profile_root / ".cursor")


def test_cursor_driver_supports_private_whisper_context(tmp_path: Path) -> None:
    fake = FakeProcessManager([CompletedInvocation(0, _success("秘密は守るよ"), "")])
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    response = asyncio.run(driver.think(
        "INV-CURSOR-WHISPER",
        "whisper",
        {"name": "Kina", "persona": "穏やかに話す。"},
        {
            "private_context": "前回の秘密",
            "recent_whispers": [{"from": "master", "to": "Kina", "text": "内緒"}],
            "current_whisper_history": [{"from": "master", "to": "Kina", "text": "今日の秘密"}],
            "public_history": [
                {"from": "Lapan", "text": "昔の公開話"},
                {"from": "master", "text": "公開話"},
            ],
            "current_residents": ["Cursor", "Gemini", "Codex"],
        },
    ))

    assert response.say == "秘密は守るよ"
    prompt = str(fake.calls[0]["stdin_text"])
    assert "1対1のWhisper" in prompt
    assert "前回の秘密" in prompt
    assert "今日の秘密" in prompt
    assert "公開話" in prompt
    assert "現在このWorldにいるResident:\nCursor / Gemini / Codex" in prompt
    assert "現在一覧にいないResident名" in prompt
    assert "Lapan: 昔の公開話" in prompt


def test_cursor_driver_builds_resident_chat_prompt_for_counterpart_without_private_context(tmp_path: Path) -> None:
    fake = FakeProcessManager([CompletedInvocation(0, _success("返事"), "")])
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    response = asyncio.run(driver.think(
        "INV-CURSOR-RESIDENT-CHAT",
        "talk",
        {"name": "Kina", "persona": "穏やかに話す。"},
        {
            "conversation_kind": "resident_chat",
            "counterpart": "Lapan",
            "history": [{"kind": "resident_chat", "from": "Lapan", "to": "Kina", "text": "話そう"}],
        },
    ))

    assert response.say == "返事"
    prompt = str(fake.calls[0]["stdin_text"])
    assert "Resident「Lapan」との公開会話" in prompt
    assert "Lapan → Kina: 話そう" in prompt
    assert "Masterの最新発話へ自然に返事" not in prompt
    assert "Private Memory" in prompt


def test_cursor_driver_retries_invalid_json_once(tmp_path: Path) -> None:
    fake = FakeProcessManager([
        CompletedInvocation(0, json.dumps({"type": "result", "result": "bad"}), ""),
        CompletedInvocation(0, _success("再試行成功"), ""),
    ])
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    response = asyncio.run(driver.think("INV-CURSOR-RETRY", "talk", {"name": "Kina"}, {"history": []}))

    assert response.say == "再試行成功"
    assert len(fake.calls) == 2


def test_cursor_driver_raises_after_second_invalid_response(tmp_path: Path) -> None:
    fake = FakeProcessManager([
        CompletedInvocation(0, "bad", ""),
        CompletedInvocation(0, "still-bad", ""),
    ])
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    with pytest.raises(BrainResponseError):
        asyncio.run(driver.think("INV-CURSOR-BAD", "talk", {"name": "Kina"}, {"history": []}))


def test_cursor_driver_cancel_targets_same_invocation(tmp_path: Path) -> None:
    fake = FakeProcessManager([])
    driver = CursorDriver(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    assert asyncio.run(driver.cancel("INV-CURSOR-CANCEL")) is True
    assert fake.cancelled == ["INV-CURSOR-CANCEL"]
