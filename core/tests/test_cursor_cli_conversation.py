from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from core.agents.base import AgentRunRequest, AgentRuntimeProtocolError
from core.agents.cursor_cli_conversation import CursorCliConversationAdapter
from core.brains.process_manager import CompletedInvocation


class FakeProcessManager:
    def __init__(self, outputs: list[CompletedInvocation]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
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


def _result(text: str, session_id: str) -> CompletedInvocation:
    return CompletedInvocation(0, json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": text,
        "session_id": session_id,
    }), "")


def _request(tmp_path: Path, *, session_id: str | None = None) -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "BC-TEST"
    working.mkdir(parents=True, exist_ok=True)
    return AgentRunRequest(
        task_id="BC-TEST",
        agent_session_id="INV-CURSOR-NATIVE",
        resident="Cursor",
        provider="cursor",
        prompt='{"say":"probe"}',
        working_dir=working,
        model="cursor-grok-4.6-xhigh",
        read_only=True,
        purpose="resident_brain",
        conversation_id="chat:S-1:public:Cursor",
        provider_session_id=session_id,
    )


def test_cursor_cli_native_uses_exact_xhigh_non_fast_and_returns_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    fake = FakeProcessManager([_result('{"say":"ok","actions":[],"pass":false}', "cursor-session-1")])
    adapter = CursorCliConversationAdapter(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )
    events: list[tuple[str, dict[str, object]]] = []

    async def emit(event_type, payload):
        events.append((event_type, payload))

    async def no_master(*_args, **_kwargs):
        raise AssertionError("Master input was requested")

    raw = asyncio.run(adapter.run(_request(tmp_path), emit=emit, wait_for_master=no_master))

    assert raw == '{"say":"ok","actions":[],"pass":false}'
    argv = fake.calls[0]["argv"]
    assert isinstance(argv, tuple)
    assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"
    assert "--resume" not in argv
    if os.name == "nt":
        assert "--sandbox" not in argv
    else:
        assert argv[argv.index("--sandbox") + 1] == "enabled"
    assert not any("fast" in item.casefold() for item in argv)
    env = fake.calls[0]["env"]
    assert isinstance(env, dict)
    assert "GEMINI_API_KEY" not in env
    assert any(
        event_type == "run_state" and payload.get("provider_session_id") == "cursor-session-1"
        for event_type, payload in events
    )


def test_cursor_cli_native_resumes_same_session_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    fake = FakeProcessManager([_result('{"say":"continued","actions":[],"pass":false}', "cursor-session-1")])
    adapter = CursorCliConversationAdapter(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    async def emit(_event_type, _payload):
        return None

    async def no_master(*_args, **_kwargs):
        raise AssertionError("Master input was requested")

    raw = asyncio.run(adapter.run(
        _request(tmp_path, session_id="cursor-session-1"),
        emit=emit,
        wait_for_master=no_master,
    ))

    assert "continued" in raw
    argv = fake.calls[0]["argv"]
    assert isinstance(argv, tuple)
    assert argv[argv.index("--resume") + 1] == "cursor-session-1"
    assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"


def test_cursor_cli_native_rejects_resume_session_id_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    fake = FakeProcessManager([_result("{}", "different-session")])
    adapter = CursorCliConversationAdapter(
        tmp_path,
        process_manager=fake,  # type: ignore[arg-type]
        command_prefix=("node.exe", "cursor-index.js"),
    )

    async def emit(_event_type, _payload):
        return None

    async def no_master(*_args, **_kwargs):
        return {}

    with pytest.raises(AgentRuntimeProtocolError, match="different session_id"):
        asyncio.run(adapter.run(
            _request(tmp_path, session_id="cursor-session-1"),
            emit=emit,
            wait_for_master=no_master,
        ))
