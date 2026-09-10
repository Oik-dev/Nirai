from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import PermissionRuleValue, PermissionUpdate, ToolPermissionContext

from core.agents import AgentRuntimeManager
from core.agents.base import AgentRunRequest, AgentRuntimeError, AgentRuntimeProtocolError
from core.agents.claude_agent import ClaudeAgentSdkAdapter
from core.agents.safety import AgentWorkspacePolicy


def _policy(tmp_path: Path) -> AgentWorkspacePolicy:
    return AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))


def _request(tmp_path: Path, *, reasoning: str | None = "high") -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "TASK-CLAUDE"
    working.mkdir(parents=True, exist_ok=True)
    (working / "task.md").write_text("make result.txt\n", encoding="utf-8")
    return AgentRunRequest(
        task_id="TASK-CLAUDE",
        agent_session_id="AS-CLAUDE",
        resident="Claude",
        provider="claude-code",
        prompt="make result.txt",
        working_dir=working,
        model="sonnet",
        reasoning_effort=reasoning,
    )


class FakeClaudeClient:
    def __init__(self, options, messages=None) -> None:
        self.options = options
        self.messages = list(messages or [])
        self.connected = False
        self.disconnected = False
        self.interrupted = False
        self.queries: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    async def query(self, prompt: str) -> None:
        self.queries.append(prompt)

    async def receive_response(self):
        for message in self.messages:
            yield message

    async def interrupt(self) -> None:
        self.interrupted = True

    async def disconnect(self) -> None:
        self.disconnected = True


def test_claude_agent_options_isolate_settings_tools_and_secrets(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    monkeypatch.setenv("NIRAI_SECRET_SENTINEL", "must-not-leak")

    async def scenario() -> None:
        request = _request(tmp_path)
        captured: list[FakeClaudeClient] = []
        messages = [
            SystemMessage(subtype="init", data={"session_id": "claude-session-1"}),
            AssistantMessage(
                content=[ThinkingBlock(thinking="private", signature="sig"), TextBlock(text="working")],
                model="sonnet",
            ),
            ResultMessage(
                subtype="success",
                duration_ms=1,
                duration_api_ms=1,
                is_error=False,
                num_turns=1,
                session_id="claude-session-1",
                result="done",
            ),
        ]

        def factory(options):
            client = FakeClaudeClient(options, messages)
            captured.append(client)
            return client

        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path), client_factory=factory)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def no_wait(*args):
            raise AssertionError("no tool permission expected")

        result = await adapter.run(request, emit=emit, wait_for_master=no_wait)

        assert result == "done"
        client = captured[0]
        options = client.options
        assert client.connected and client.disconnected
        assert options.cwd == request.working_dir
        assert options.setting_sources == []
        assert options.skills == []
        assert options.plugins == []
        assert options.mcp_servers == {}
        assert options.strict_mcp_config is True
        assert options.allowed_tools == []
        assert set(options.tools) == {"Read", "Grep", "Glob", "Edit", "Write", "Bash", "AskUserQuestion"}
        assert {"WebFetch", "WebSearch", "Agent", "Skill"} <= set(options.disallowed_tools)
        assert options.extra_args["restricted"] is None
        assert options.extra_args["safe-mode"] is None
        assert options.extra_args["no-session-persistence"] is None
        assert options.extra_args["permission-prompts"] == "host"
        assert options.env["ANTHROPIC_API_KEY"] == ""
        assert options.env["ANTHROPIC_AUTH_TOKEN"] == ""
        assert options.env["GEMINI_API_KEY"] == ""
        assert options.env["NIRAI_SECRET_SENTINEL"] == ""
        assert options.model == "sonnet"
        assert options.effort == "high"
        assert client.queries and "make result.txt" in client.queries[0]
        assert not any("private" in str(payload) for _, payload in emitted)
        assert any(kind == "assistant_message" and payload.get("text") == "done" for kind, payload in emitted)
        assert not (tmp_path / "runtime" / "claude_agent_sessions" / "AS-CLAUDE").exists()

    asyncio.run(scenario())


def test_claude_agent_refuses_xhigh_silent_fallback(tmp_path: Path) -> None:
    adapter = ClaudeAgentSdkAdapter(_policy(tmp_path), client_factory=lambda options: FakeClaudeClient(options))
    with pytest.raises(AgentRuntimeProtocolError, match="silently downgrade"):
        asyncio.run(adapter.run(
            _request(tmp_path, reasoning="xhigh"),
            emit=lambda *args: None,  # type: ignore[arg-type]
            wait_for_master=lambda *args: None,  # type: ignore[arg-type]
        ))


def test_claude_file_permission_requires_master_and_correlates_diff(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "result.txt"
        target.write_text("before\n", encoding="utf-8")
        emitted: list[tuple[str, dict[str, Any]]] = []
        context = ToolPermissionContext(tool_use_id="tool-edit-1", title="Edit result")

        async def emit(kind, payload):
            emitted.append((kind, payload))

        async def approve(request_id, kind, payload):
            assert request_id == "tool-edit-1"
            assert kind == "approval"
            assert payload["operation_id"] == "tool-edit-1"
            assert payload["options"] == ["approve_once", "reject", "cancel"]
            return {"decision": "approve_once"}

        result = await adapter._handle_permission(
            request,
            "Edit",
            {"file_path": str(target), "old_string": "before", "new_string": "after"},
            context,
            emit=emit,
            wait_for_master=approve,
        )
        assert result.behavior == "allow"
        assert [kind for kind, _ in emitted] == ["file_change"]
        payload = emitted[0][1]
        assert payload["operation_id"] == "tool-edit-1"
        assert payload["changes"][0]["relative_path"] == "result.txt"
        assert "+after" in payload["changes"][0]["diff"]
        assert target.read_text(encoding="utf-8") == "before\n"

    asyncio.run(scenario())


def test_claude_file_permission_rejects_outside_and_task_metadata_before_master(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []
        waited = False

        async def emit(kind, payload):
            emitted.append((kind, payload))

        async def wait(*args):
            nonlocal waited
            waited = True
            return {"decision": "approve_once"}

        outside = await adapter._handle_permission(
            request,
            "Write",
            {"file_path": str(tmp_path / "outside.txt"), "content": "bad"},
            ToolPermissionContext(tool_use_id="outside"),
            emit=emit,
            wait_for_master=wait,
        )
        assert outside.behavior == "deny"
        assert waited is False
        assert any(kind == "error" and payload.get("code") == "claude_file_change_outside_workspace" for kind, payload in emitted)

        metadata = await adapter._handle_permission(
            request,
            "Write",
            {"file_path": str(request.working_dir / "task.md"), "content": "tamper"},
            ToolPermissionContext(tool_use_id="task-md"),
            emit=emit,
            wait_for_master=wait,
        )
        assert metadata.behavior == "deny"
        assert waited is False

    asyncio.run(scenario())


def test_claude_session_approval_uses_only_provider_suggestion_as_session_update(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "new.txt"
        suggestion = PermissionUpdate(
            type="addRules",
            rules=[PermissionRuleValue(tool_name="Write", rule_content=str(target))],
            behavior="allow",
            destination="localSettings",
        )
        context = ToolPermissionContext(tool_use_id="write-session", suggestions=[suggestion])

        async def emit(*args):
            return None

        async def approve_session(request_id, kind, payload):
            assert "approve_session" in payload["options"]
            return {"decision": "approve_session"}

        result = await adapter._handle_permission(
            request,
            "Write",
            {"file_path": str(target), "content": "ok"},
            context,
            emit=emit,
            wait_for_master=approve_session,
        )
        assert result.behavior == "allow"
        assert result.updated_permissions
        assert result.updated_permissions[0].destination == "session"
        assert suggestion.destination == "localSettings"

    asyncio.run(scenario())


def test_claude_session_approval_rejects_broad_or_cross_tool_permission_suggestions(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "new.txt"
        suggestions = [
            PermissionUpdate(type="setMode", mode="bypassPermissions", destination="localSettings"),
            PermissionUpdate(
                type="addRules",
                rules=[PermissionRuleValue(tool_name="Bash", rule_content="*")],
                behavior="allow",
                destination="localSettings",
            ),
        ]
        seen_options: list[str] = []

        async def emit(*args):
            return None

        async def approve_once(request_id, kind, payload):
            seen_options.extend(payload["options"])
            return {"decision": "approve_once"}

        result = await adapter._handle_permission(
            request,
            "Write",
            {"file_path": str(target), "content": "ok"},
            ToolPermissionContext(tool_use_id="write-no-broad-session", suggestions=suggestions),
            emit=emit,
            wait_for_master=approve_once,
        )
        assert result.behavior == "allow"
        assert "approve_session" not in seen_options

    asyncio.run(scenario())


def test_claude_bash_is_always_master_gated_and_unsandboxed_request_is_denied(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind, payload):
            emitted.append((kind, payload))

        async def reject(request_id, kind, payload):
            assert kind == "approval"
            assert payload["command"] == "python -m pytest"
            return {"decision": "reject"}

        denied = await adapter._handle_permission(
            request,
            "Bash",
            {"command": "python -m pytest"},
            ToolPermissionContext(tool_use_id="bash-1"),
            emit=emit,
            wait_for_master=reject,
        )
        assert denied.behavior == "deny"
        assert any(kind == "command_execution" and payload.get("status") == "pending_approval" for kind, payload in emitted)

        unsafe = await adapter._handle_permission(
            request,
            "Bash",
            {"command": "echo bad", "dangerouslyDisableSandbox": True},
            ToolPermissionContext(tool_use_id="bash-unsafe"),
            emit=emit,
            wait_for_master=reject,
        )
        assert unsafe.behavior == "deny"
        assert unsafe.interrupt is True

    asyncio.run(scenario())


def test_claude_read_callback_and_subagent_are_fail_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)

        async def emit(*args):
            return None

        async def wait(*args):
            raise AssertionError("read/subagent deny must not reach Master")

        read = await adapter._handle_permission(
            request, "Read", {"file_path": str(request.working_dir / "task.md")},
            ToolPermissionContext(tool_use_id="read-outside-policy"), emit=emit, wait_for_master=wait,
        )
        assert read.behavior == "deny"
        subagent = await adapter._handle_permission(
            request, "Write", {"file_path": str(request.working_dir / "x.txt"), "content": "x"},
            ToolPermissionContext(tool_use_id="subagent", agent_id="agent-child"), emit=emit, wait_for_master=wait,
        )
        assert subagent.behavior == "deny"

    asyncio.run(scenario())


def test_claude_question_maps_choices_and_free_text_to_provider_answers(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        captured: dict[str, Any] = {}

        async def emit(kind, payload):
            captured["payload"] = payload

        async def answer(request_id, kind, payload):
            assert kind == "question"
            return {"answers": {"q1": ["q1-o2"]}}

        result = await adapter._handle_permission(
            request,
            "AskUserQuestion",
            {"questions": [{
                "question": "Which mode?",
                "header": "Mode",
                "options": [
                    {"label": "Safe", "description": "safer"},
                    {"label": "Fast", "description": "faster"},
                ],
                "multiSelect": False,
            }]},
            ToolPermissionContext(tool_use_id="ask-1"),
            emit=emit,
            wait_for_master=answer,
        )
        assert result.behavior == "allow"
        assert result.updated_input["answers"] == {"Which mode?": "Fast"}
        question = captured["payload"]["questions"][0]
        assert question["allow_multiple"] is False
        assert question["allow_free_text"] is True

    asyncio.run(scenario())


def test_claude_question_accepts_master_free_text_answer(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path))
        request = _request(tmp_path)

        async def emit(*args):
            return None

        async def answer(request_id, kind, payload):
            return {"answers": {"q1": ["A custom answer"]}}

        result = await adapter._handle_permission(
            request,
            "AskUserQuestion",
            {"questions": [{
                "question": "What should I use?",
                "options": [{"label": "Default", "description": "default"}],
                "multiSelect": False,
            }]},
            ToolPermissionContext(tool_use_id="ask-free"),
            emit=emit,
            wait_for_master=answer,
        )
        assert result.behavior == "allow"
        assert result.updated_input["answers"] == {"What should I use?": "A custom answer"}

    asyncio.run(scenario())


def test_claude_tool_messages_normalize_command_file_and_hide_thought(tmp_path: Path) -> None:
    async def scenario() -> None:
        request = _request(tmp_path)
        messages = [
            SystemMessage(subtype="init", data={"session_id": "claude-session-events"}),
            AssistantMessage(
                content=[
                    ThinkingBlock(thinking="do not persist", signature="sig"),
                    ToolUseBlock(id="bash-1", name="Bash", input={"command": "echo hi"}),
                    ToolUseBlock(id="write-1", name="Write", input={"file_path": str(request.working_dir / "a.txt"), "content": "a"}),
                ],
                model="sonnet",
            ),
            UserMessage(
                content=[
                    ToolResultBlock(tool_use_id="bash-1", content="hi", is_error=False),
                    ToolResultBlock(tool_use_id="write-1", content="written", is_error=False),
                ]
            ),
            ResultMessage(
                subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
                num_turns=1, session_id="claude-session-events", result="finished",
            ),
        ]
        clients: list[FakeClaudeClient] = []

        def factory(options):
            client = FakeClaudeClient(options, messages)
            clients.append(client)
            return client

        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path), client_factory=factory)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(kind, payload):
            emitted.append((kind, payload))

        async def no_wait(*args):
            raise AssertionError("fake stream does not execute permission callbacks")

        await adapter.run(request, emit=emit, wait_for_master=no_wait)
        kinds = [kind for kind, _ in emitted]
        assert kinds.count("tool_call") == 2
        assert "command_execution" in kinds
        assert "file_change" in kinds
        assert "assistant_message" in kinds
        assert "do not persist" not in str(emitted)

    asyncio.run(scenario())


def test_claude_disconnect_runs_even_when_connect_fails(tmp_path: Path) -> None:
    class FailingConnectClient(FakeClaudeClient):
        async def connect(self) -> None:
            self.connected = True
            raise OSError("connect failed after process spawn")

    async def scenario() -> None:
        clients: list[FailingConnectClient] = []

        def factory(options):
            client = FailingConnectClient(options)
            clients.append(client)
            return client

        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path), client_factory=factory)

        async def emit(*args):
            return None

        async def wait(*args):
            raise AssertionError("no permission expected")

        with pytest.raises(Exception):
            await adapter.run(_request(tmp_path), emit=emit, wait_for_master=wait)
        assert clients[0].disconnected is True
        assert not (tmp_path / "runtime" / "claude_agent_sessions" / "AS-CLAUDE").exists()

    asyncio.run(scenario())


def test_default_agent_runtime_advertises_claude_with_precise_capabilities(tmp_path: Path) -> None:
    manager = AgentRuntimeManager(tmp_path, ("runtime\\workspace",))
    assert manager.supports_provider("claude-code") is True
    assert manager.provider_capabilities("claude-code") == frozenset({
        "approval",
        "question",
        "file_diff",
        "command_result",
    })


def test_claude_cancel_interrupts_active_sdk_client(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = ClaudeAgentSdkAdapter(_policy(tmp_path), client_factory=lambda options: FakeClaudeClient(options))
        client = FakeClaudeClient(None)
        adapter._active["AS-CANCEL"] = client
        assert await adapter.cancel("AS-CANCEL") is True
        assert client.interrupted is True
        assert await adapter.cancel("missing") is False

    asyncio.run(scenario())
