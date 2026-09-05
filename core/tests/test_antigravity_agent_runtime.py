from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.agents import AgentRuntimeManager
from core.agents.antigravity_agent import (
    ANTIGRAVITY_COMMAND_OUTPUT_LIMIT,
    ANTIGRAVITY_DIFF_LIMIT,
    AntigravityAgentAdapter,
    _review_diff,
)
from core.agents.base import (
    AgentRunRequest,
    AgentRuntimeError,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
)
from core.agents.safety import AgentSafetyError, AgentWorkspacePolicy
from core.brains.base import BrainUnavailableError


def _root(tmp_path: Path) -> Path:
    world = tmp_path / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / ".env").write_text("GEMINI_API_KEY=test-key\n", encoding="utf-8")
    return tmp_path


def _policy(tmp_path: Path) -> AgentWorkspacePolicy:
    return AgentWorkspacePolicy(_root(tmp_path), ("runtime\\workspace",))


def _request(tmp_path: Path, *, model: str = "antigravity-preview-05-2026") -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "TASK-AG"
    working.mkdir(parents=True, exist_ok=True)
    (working / "task.md").write_text("make result.txt\n", encoding="utf-8")
    return AgentRunRequest(
        task_id="TASK-AG",
        agent_session_id="AS-AG",
        resident="Gemini",
        provider="gemini",
        prompt="make result.txt",
        working_dir=working,
        model=model,
    )


def _completed(interaction_id: str = "int-final", environment_id: str = "env-1", text: str = "done") -> dict[str, Any]:
    return {
        "id": interaction_id,
        "environment_id": environment_id,
        "status": "completed",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
    }


def _requires(call: dict[str, Any], interaction_id: str = "int-1", environment_id: str = "env-1") -> dict[str, Any]:
    return {
        "id": interaction_id,
        "environment_id": environment_id,
        "status": "requires_action",
        "steps": [
            {"type": "thought", "summary": [{"type": "text", "text": "PRIVATE-THOUGHT"}]},
            call,
        ],
    }


def _call(name: str, arguments: dict[str, Any], call_id: str = "fc-1") -> dict[str, Any]:
    return {"type": "function_call", "id": call_id, "name": name, "arguments": arguments}


async def _run_with_fake_api(
    monkeypatch: pytest.MonkeyPatch,
    adapter: AntigravityAgentAdapter,
    request: AgentRunRequest,
    responses: list[dict[str, Any]],
    *,
    approval=None,
):
    calls: list[tuple[str, str | None, dict[str, Any] | None]] = []
    queue = list(responses)
    environment_id = next(
        (
            value
            for response in responses
            for value in [response.get("environment_id")]
            if isinstance(value, str) and value
        ),
        "env-1",
    )

    async def fake_request(api_key, path, payload=None, *, method=None):
        calls.append((path, method, payload))
        if method == "DELETE":
            return {}
        if path.endswith("/cancel"):
            return {}
        if path.startswith("/environments?page_size=1000") and method == "GET":
            return {"environments": []}
        if path == "/environments" and payload is not None:
            return {"id": environment_id, "sources": payload.get("sources", [])}
        if path.startswith("/interactions"):
            if not queue:
                raise AssertionError(f"unexpected interaction request: {path} {payload}")
            return queue.pop(0)
        raise AssertionError(f"unexpected API path: {path}")

    monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)
    emitted: list[tuple[str, dict[str, Any]]] = []

    async def emit(kind, payload):
        emitted.append((kind, dict(payload)))

    async def wait_for_master(request_id, kind, payload):
        if approval is None:
            raise AssertionError(f"unexpected Master request: {kind} {payload}")
        return await approval(request_id, kind, payload)

    result = await adapter.run(request, emit=emit, wait_for_master=wait_for_master)
    return result, calls, emitted


def test_antigravity_initial_request_is_network_disabled_and_local_bridge_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        result, calls, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [_completed()],
        )
        assert result == "done"
        environment_create = next(
            payload
            for path, _, payload in calls
            if path == "/environments" and payload is not None
        )
        assert environment_create["network"] == "disabled"
        assert environment_create["sources"][0]["type"] == "inline"
        assert environment_create["sources"][0]["target"] == "nirai-session-owner.txt"
        assert environment_create["sources"][0]["content"].startswith("nirai:AS-AG:")
        initial = next(
            payload
            for path, _, payload in calls
            if path == "/interactions" and payload and "previous_interaction_id" not in payload
        )
        assert initial["agent"] == "antigravity-preview-05-2026"
        assert initial["environment"] == "env-1"
        assert initial["background"] is True
        assert initial["store"] is True
        assert "labels" not in initial
        tool_names = {
            item["name"]
            for item in initial["tools"]
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        assert tool_names == {
            "nirai_list_files",
            "nirai_read_text_file",
            "nirai_write_text_file",
            "nirai_edit_text_file",
            "nirai_delete_file",
            "nirai_ask_master",
            "nirai_submit_plan",
        }
        assert {item.get("type") for item in initial["tools"]} >= {"function", "code_execution"}
        assert "google_search" not in str(initial["tools"])
        assert "remote filesystem is scratch space only" in initial["system_instruction"]
        delete_calls = {(path, method) for path, method, _ in calls if method == "DELETE"}
        assert ("/environments/env-1", "DELETE") in delete_calls
        assert ("/interactions/int-final", "DELETE") in delete_calls
        assert not any("PRIVATE-THOUGHT" in str(payload) for _, payload in emitted)

    asyncio.run(scenario())


def test_antigravity_poll_emits_running_and_remote_command_steps_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        monkeypatch.setattr("core.agents.antigravity_agent.GEMINI_POLL_INTERVAL_SEC", 0.0)
        call = {
            "type": "code_execution_call",
            "id": "code-poll",
            "arguments": {"code": "print(2 + 2)", "language": "python"},
        }
        initial = {
            "id": "int-poll",
            "status": "in_progress",
            "steps": [call],
        }
        completed = {
            "id": "int-poll",
            "environment_id": "env-poll",
            "status": "completed",
            "steps": [
                {"type": "code_execution_result", "call_id": "code-poll", "result": "4\n"},
                {"type": "model_output", "content": [{"type": "text", "text": "done"}]},
            ],
        }
        result, _, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [initial, completed],
        )
        assert result == "done"
        running_states = [payload for kind, payload in emitted if kind == "run_state" and payload.get("state") == "running"]
        assert len(running_states) == 2
        assert "provider_session_id" not in running_states[0]
        assert running_states[1]["provider_session_id"] == "env-poll"
        assert all(payload["provider_turn_id"] == "int-poll" for payload in running_states)
        commands = [payload for kind, payload in emitted if kind == "command_execution"]
        assert [payload["status"] for payload in commands] == ["running", "completed"]
        assert commands[1]["command"] == "print(2 + 2)"
        assert commands[1]["language"] == "python"
        assert commands[1]["output"] == "4\n"

    asyncio.run(scenario())


def test_antigravity_incomplete_continues_same_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        incomplete = {
            "id": "int-incomplete",
            "environment_id": "env-incomplete",
            "status": "incomplete",
            "steps": [],
        }
        result, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [incomplete, _completed("int-final", "env-incomplete", "continued")],
        )
        assert result == "continued"
        continuation = next(
            payload
            for path, _, payload in calls
            if path == "/interactions"
            and payload
            and payload.get("previous_interaction_id") == "int-incomplete"
        )
        assert continuation["agent"] == "antigravity-preview-05-2026"
        assert continuation["environment"] == "env-incomplete"
        assert continuation["background"] is True
        assert continuation["store"] is True
        assert continuation["system_instruction"]
        assert {item.get("type") for item in continuation["tools"]} >= {"function", "code_execution"}
        assert "google_search" not in str(continuation["tools"])
        assert "Continue the Nirai Task" in continuation["input"]

    asyncio.run(scenario())


def test_antigravity_terminal_without_environment_uses_preowned_environment_for_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        terminal = {
            "id": "int-no-env",
            "status": "completed",
            "steps": [{"type": "model_output", "content": [{"type": "text", "text": "done"}]}],
        }
        result, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [terminal],
        )
        assert result == "done"
        delete_calls = {(path, method) for path, method, _ in calls if method == "DELETE"}
        assert ("/environments/env-1", "DELETE") in delete_calls
        assert ("/interactions/int-no-env", "DELETE") in delete_calls

    asyncio.run(scenario())


def test_antigravity_recovers_environment_when_create_response_is_lost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        calls: list[tuple[str, str | None, dict[str, Any] | None]] = []
        marker: str | None = None
        list_calls = 0

        async def fake_request(api_key, path, payload=None, *, method=None):
            nonlocal marker, list_calls
            calls.append((path, method, payload))
            if method == "DELETE":
                return {}
            if path.startswith("/environments?page_size=1000") and method == "GET":
                list_calls += 1
                if list_calls == 1:
                    return {"environments": []}
                return {
                    "environments": [{"environment_id": "env-recovered", "type": "remote"}],
                    "next_page_token": "",
                }
            if path == "/environments" and payload is not None:
                marker = payload["sources"][0]["content"]
                raise BrainUnavailableError("environment create response lost")
            if path == "/environments/env-recovered" and method == "GET":
                assert marker is not None
                return {
                    "environment_id": "env-recovered",
                    "type": "remote",
                    "sources": [{
                        "type": "inline",
                        "content": marker,
                        "target": "nirai-session-owner.txt",
                    }],
                }
            if path == "/interactions":
                assert payload["environment"] == "env-recovered"
                return _completed("int-recovered", "env-recovered", "recovered")
            raise AssertionError(f"unexpected API path: {path}")

        monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)

        async def emit(*args):
            return None

        async def wait(*args):
            raise AssertionError("no Master request expected")

        result = await adapter.run(_request(tmp_path), emit=emit, wait_for_master=wait)
        assert result == "recovered"
        delete_calls = {(path, method) for path, method, _ in calls if method == "DELETE"}
        assert ("/environments/env-recovered", "DELETE") in delete_calls
        assert ("/interactions/int-recovered", "DELETE") in delete_calls

    asyncio.run(scenario())


def test_antigravity_environment_list_accepts_both_published_id_shapes_with_pagination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        paths: list[str] = []

        async def fake_request(api_key, path, payload=None, *, method=None):
            assert method == "GET"
            paths.append(path)
            if path == "/environments?page_size=1000":
                return {
                    "environments": [{"environment_id": "env-managed-guide", "type": "remote"}],
                    "next_page_token": "next token",
                }
            if path == "/environments?page_size=1000&page_token=next%20token":
                return {
                    "environments": [{"id": "env-api-reference", "status": "active"}],
                    "next_page_token": "",
                }
            raise AssertionError(f"unexpected API path: {path}")

        monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)
        assert await adapter._list_remote_environment_ids() == {
            "env-managed-guide",
            "env-api-reference",
        }
        assert paths == [
            "/environments?page_size=1000",
            "/environments?page_size=1000&page_token=next%20token",
        ]

    asyncio.run(scenario())


def test_antigravity_interaction_create_response_loss_still_deletes_known_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        calls: list[tuple[str, str | None, dict[str, Any] | None]] = []

        async def fake_request(api_key, path, payload=None, *, method=None):
            calls.append((path, method, payload))
            if method == "DELETE":
                return {}
            if path.startswith("/environments?page_size=1000") and method == "GET":
                return {"environments": []}
            if path == "/environments" and payload is not None:
                return {"id": "env-known", "sources": payload.get("sources", [])}
            if path == "/interactions":
                raise BrainUnavailableError("interaction create response lost")
            raise AssertionError(f"unexpected API path: {path}")

        monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)

        async def emit(*args):
            return None

        async def wait(*args):
            raise AssertionError("no Master request expected")

        with pytest.raises(AgentRuntimeUnavailableError, match="interaction create response lost"):
            await adapter.run(_request(tmp_path), emit=emit, wait_for_master=wait)

        delete_calls = {(path, method) for path, method, _ in calls if method == "DELETE"}
        assert ("/environments/env-known", "DELETE") in delete_calls
        assert not any(path.startswith("/interactions/") and method == "DELETE" for path, method, _ in calls)

    asyncio.run(scenario())


def test_antigravity_manager_file_approval_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
        adapter = AntigravityAgentAdapter(policy)
        responses = [
            _requires(
                _call(
                    "nirai_write_text_file",
                    {"path": "manager-result.txt", "content": "approved\n"},
                    "fc-manager",
                ),
                environment_id="env-manager",
            ),
            _completed("int-manager-final", "env-manager", "manager done"),
        ]

        async def fake_request(api_key, path, payload=None, *, method=None):
            if method == "DELETE":
                return {}
            if path.endswith("/cancel"):
                return {}
            if path.startswith("/environments?page_size=1000") and method == "GET":
                return {"environments": []}
            if path == "/environments" and payload is not None:
                environment_id = next(
                    response.get("environment_id")
                    for response in responses
                    if isinstance(response.get("environment_id"), str)
                )
                return {"id": environment_id, "sources": payload.get("sources", [])}
            if path.startswith("/interactions"):
                return responses.pop(0)
            raise AssertionError(f"unexpected API path: {path}")

        monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)
        observed: list[str] = []
        manager = AgentRuntimeManager(
            root,
            ("runtime\\workspace",),
            adapters={"gemini": adapter},
        )

        async def broadcast(event):
            observed.append(event.type)
            if event.type == "approval_request":
                accepted = await manager.respond(
                    event.agent_session_id,
                    event.payload["request_id"],
                    "approval",
                    {"decision": "approve_once"},
                )
                assert accepted is True

        manager.set_broadcast(broadcast)
        snapshot = await manager.start_session(
            task_id="TASK-MANAGER-AG",
            resident="Gemini",
            provider="gemini",
            prompt="create manager-result.txt",
            model="antigravity-preview-05-2026",
        )
        for _ in range(200):
            payload = manager.snapshot_payload(snapshot.agent_session_id)
            if payload["session"]["run_state"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            await asyncio.sleep(0.01)
        payload = manager.snapshot_payload(snapshot.agent_session_id)
        errors = [event for event in payload["events"] if event["type"] == "error"]
        assert payload["session"]["run_state"] == "completed", errors
        assert "approval_request" in observed
        assert "file_change" in observed
        target = Path(payload["session"]["working_dir"]) / "manager-result.txt"
        assert target.read_text(encoding="utf-8") == "approved\n"

    asyncio.run(scenario())


def test_antigravity_manager_preserves_full_approval_diff_above_generic_string_cap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
        adapter = AntigravityAgentAdapter(policy)
        content = "".join(
            f"keep-{index:04d}-{'x' * 28}\n"
            for index in range(430)
        )
        expected_diff = _review_diff("", content, "manager-large.txt")
        assert 12_000 < len(expected_diff) <= ANTIGRAVITY_DIFF_LIMIT
        responses = [
            _requires(
                _call(
                    "nirai_write_text_file",
                    {"path": "manager-large.txt", "content": content},
                    "fc-manager-large",
                ),
                environment_id="env-manager-large",
            ),
            _completed("int-manager-large-final", "env-manager-large", "manager large done"),
        ]

        async def fake_request(api_key, path, payload=None, *, method=None):
            if method == "DELETE":
                return {}
            if path.endswith("/cancel"):
                return {}
            if path.startswith("/environments?page_size=1000") and method == "GET":
                return {"environments": []}
            if path == "/environments" and payload is not None:
                environment_id = next(
                    response.get("environment_id")
                    for response in responses
                    if isinstance(response.get("environment_id"), str)
                )
                return {"id": environment_id, "sources": payload.get("sources", [])}
            if path.startswith("/interactions"):
                return responses.pop(0)
            raise AssertionError(f"unexpected API path: {path}")

        monkeypatch.setattr("core.agents.antigravity_agent._request_json_async", fake_request)
        manager = AgentRuntimeManager(
            root,
            ("runtime\\workspace",),
            adapters={"gemini": adapter},
        )

        async def broadcast(event):
            if event.type == "approval_request":
                persisted = manager.snapshot_payload(event.agent_session_id)
                proposed = next(
                    item
                    for item in persisted["events"]
                    if item["type"] == "file_change"
                    and item["payload"].get("operation_id") == "fc-manager-large"
                    and item["payload"].get("status") == "pending_approval"
                )
                assert proposed["payload"]["changes"][0]["diff"] == expected_diff
                accepted = await manager.respond(
                    event.agent_session_id,
                    event.payload["request_id"],
                    "approval",
                    {"decision": "approve_once"},
                )
                assert accepted is True

        manager.set_broadcast(broadcast)
        snapshot = await manager.start_session(
            task_id="TASK-MANAGER-AG-LARGE-DIFF",
            resident="Gemini",
            provider="gemini",
            prompt="create manager-large.txt",
            model="antigravity-preview-05-2026",
        )
        for _ in range(200):
            payload = manager.snapshot_payload(snapshot.agent_session_id)
            if payload["session"]["run_state"] in {"completed", "failed", "cancelled", "interrupted"}:
                break
            await asyncio.sleep(0.01)
        payload = manager.snapshot_payload(snapshot.agent_session_id)
        errors = [event for event in payload["events"] if event["type"] == "error"]
        assert payload["session"]["run_state"] == "completed", errors
        proposed = next(
            item
            for item in payload["events"]
            if item["type"] == "file_change"
            and item["payload"].get("operation_id") == "fc-manager-large"
            and item["payload"].get("status") == "pending_approval"
        )
        assert proposed["payload"]["changes"][0]["diff"] == expected_diff
        target = Path(payload["session"]["working_dir"]) / "manager-large.txt"
        assert target.read_text(encoding="utf-8") == content
        assert "keep-0429" in proposed["payload"]["changes"][0]["diff"]

    asyncio.run(scenario())


def test_antigravity_rejects_excessive_function_calls_in_one_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        excessive = {
            "id": "int-many",
            "environment_id": "env-many",
            "status": "requires_action",
            "steps": [
                _call("nirai_read_text_file", {"path": "task.md"}, f"fc-{index}")
                for index in range(33)
            ],
        }
        with pytest.raises(AgentRuntimeProtocolError, match="more than 32"):
            await _run_with_fake_api(
                monkeypatch,
                adapter,
                _request(tmp_path),
                [excessive],
            )

    asyncio.run(scenario())


def test_antigravity_cleanup_failure_is_not_reported_as_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))

        async def fail_environment_cleanup(environment_id: str) -> None:
            raise AgentRuntimeError("environment cleanup failed")

        monkeypatch.setattr(adapter, "_delete_environment", fail_environment_cleanup)
        with pytest.raises(AgentRuntimeError, match="environment cleanup failed"):
            await _run_with_fake_api(
                monkeypatch,
                adapter,
                _request(tmp_path),
                [_completed()],
            )

    asyncio.run(scenario())


def test_antigravity_write_is_unchanged_until_master_approves_and_then_applies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "result.txt"

        async def approve(request_id, kind, payload):
            assert request_id == "fc-write"
            assert kind == "approval"
            assert payload["kind"] == "file_change"
            assert payload["options"] == ["approve_once", "reject", "cancel"]
            assert target.exists() is False
            return {"decision": "approve_once"}

        result, calls, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                _requires(_call("nirai_write_text_file", {"path": "result.txt", "content": "hello\n"}, "fc-write")),
                _completed("int-2", text="created result.txt"),
            ],
            approval=approve,
        )
        assert result == "created result.txt"
        assert target.read_text(encoding="utf-8") == "hello\n"
        proposed = next(payload for kind, payload in emitted if kind == "file_change" and payload.get("status") == "pending_approval")
        assert proposed["operation_id"] == "fc-write"
        assert proposed["changes"][0]["relative_path"] == "result.txt"
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        function_result = continuation["input"][0]
        assert function_result["call_id"] == "fc-write"
        assert function_result["result"]["ok"] is True
        assert continuation["system_instruction"]
        assert {item.get("type") for item in continuation["tools"]} >= {"function", "code_execution"}
        assert "google_search" not in str(continuation["tools"])

    asyncio.run(scenario())


def test_antigravity_approved_write_never_recreates_deleted_external_workspace_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        root = _root(tmp_path)
        project = root / "projects" / "ProjectA"
        project.mkdir(parents=True)
        policy = AgentWorkspacePolicy(
            root,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = AntigravityAgentAdapter(policy)
        request = AgentRunRequest(
            task_id="TASK-AG-EXTERNAL",
            agent_session_id="AS-AG-EXTERNAL",
            resident="Gemini",
            provider="gemini",
            prompt="create result.txt",
            working_dir=project,
            model="antigravity-preview-05-2026",
        )

        async def emit(*args):
            return None

        async def approve_then_delete_workspace(*args):
            project.rmdir()
            return {"decision": "approve_once"}

        with pytest.raises(AgentSafetyError, match="working directory does not exist"):
            await adapter._write_text_file(
                request,
                "fc-delete-root",
                {"path": "result.txt", "content": "must not recreate\n"},
                emit=emit,
                wait_for_master=approve_then_delete_workspace,
            )

        assert project.exists() is False

    asyncio.run(scenario())


def test_antigravity_rejected_write_leaves_workspace_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "result.txt"

        async def reject(*args):
            return {"decision": "reject"}

        _, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                _requires(_call("nirai_write_text_file", {"path": "result.txt", "content": "nope"})),
                _completed("int-2", text="change rejected"),
            ],
            approval=reject,
        )
        assert target.exists() is False
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["is_error"] is True
        assert "rejected" in continuation["input"][0]["result"]["error"].casefold()

    asyncio.run(scenario())


def test_antigravity_write_refuses_unreviewable_existing_file_before_master(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "binary.dat"
        target.write_bytes(b"\xff\xfe\x00\x01")
        _, calls, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                _requires(_call("nirai_write_text_file", {"path": "binary.dat", "content": "replacement"})),
                _completed("int-2", text="refused"),
            ],
        )
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["is_error"] is True
        assert "cannot review" in continuation["input"][0]["result"]["error"].casefold()
        assert target.read_bytes() == b"\xff\xfe\x00\x01"
        assert not any(kind == "approval_request" for kind, _ in emitted)

    asyncio.run(scenario())


def test_antigravity_write_detects_workspace_change_during_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        target = request.working_dir / "result.txt"
        target.write_text("before\n", encoding="utf-8")

        async def approve_after_external_change(*args):
            target.write_text("external\n", encoding="utf-8")
            return {"decision": "approve_once"}

        _, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                _requires(_call("nirai_write_text_file", {"path": "result.txt", "content": "agent\n"})),
                _completed("int-2", text="conflict noticed"),
            ],
            approval=approve_after_external_change,
        )
        assert target.read_text(encoding="utf-8") == "external\n"
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["is_error"] is True
        assert "changed while" in continuation["input"][0]["result"]["error"]

    asyncio.run(scenario())


def test_antigravity_task_metadata_and_outside_paths_fail_before_master(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario(function_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        result, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                _requires(_call(function_name, arguments)),
                _completed("int-2"),
            ],
        )
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        return continuation["input"][0]

    task_result = asyncio.run(scenario("nirai_write_text_file", {"path": "task.md", "content": "changed"}))
    assert task_result["is_error"] is True
    assert "task.md" in task_result["result"]["error"]

    outside_result = asyncio.run(scenario("nirai_read_text_file", {"path": "..\\secret.txt"}))
    assert outside_result["is_error"] is True
    assert "escaped" in outside_result["result"]["error"]


def test_antigravity_read_and_list_need_no_master(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "notes.txt").write_text("one\ntwo\nthree\n", encoding="utf-8")
        result, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            request,
            [
                {
                    "id": "int-1",
                    "environment_id": "env-1",
                    "status": "requires_action",
                    "steps": [
                        _call("nirai_list_files", {"path": "."}, "fc-list"),
                        _call("nirai_read_text_file", {"path": "notes.txt", "start_line": 2, "max_lines": 1}, "fc-read"),
                    ],
                },
                _completed("int-2", text="read complete"),
            ],
        )
        assert result == "read complete"
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert len(continuation["input"]) == 2
        listed = continuation["input"][0]["result"]
        assert any(item["name"] == "notes.txt" for item in listed["entries"])
        read = continuation["input"][1]["result"]
        assert read["content"] == "two"

    asyncio.run(scenario())


def test_antigravity_remote_code_execution_is_observable_but_never_master_approved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        output = "x" * (ANTIGRAVITY_COMMAND_OUTPUT_LIMIT + 100)
        completed = {
            "id": "int-code",
            "environment_id": "env-1",
            "status": "completed",
            "steps": [
                {
                    "type": "code_execution_call",
                    "id": "code-1",
                    "arguments": {"code": "print('hello')", "language": "python"},
                },
                {
                    "type": "code_execution_result",
                    "call_id": "code-1",
                    "result": output,
                },
                {"type": "model_output", "content": [{"type": "text", "text": "remote command complete"}]},
            ],
        }
        result, _, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [completed],
        )
        assert result == "remote command complete"
        command_events = [payload for kind, payload in emitted if kind == "command_execution"]
        assert [event["status"] for event in command_events] == ["running", "completed"]
        assert all(event["execution_scope"] == "remote_sandbox" for event in command_events)
        assert all(event["cwd"] == "Google Antigravity remote sandbox" for event in command_events)
        assert command_events[0]["command"] == "print('hello')"
        assert command_events[0]["language"] == "python"
        assert len(command_events[1]["output"]) <= ANTIGRAVITY_COMMAND_OUTPUT_LIMIT

    asyncio.run(scenario())


def test_antigravity_unknown_local_command_function_fails_closed_without_local_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        _, calls, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [
                _requires(_call("nirai_run_command", {"command": "echo no"}, "fc-command")),
                _completed("int-2", text="local command unavailable"),
            ],
        )
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["is_error"] is True
        assert "unknown" in continuation["input"][0]["result"]["error"].casefold()
        assert not any(kind == "command_execution" for kind, _ in emitted)

    asyncio.run(scenario())


def test_antigravity_question_maps_choices_and_free_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        seen: dict[str, Any] = {}

        async def answer(request_id, kind, payload):
            seen.update(payload)
            assert kind == "question"
            return {"answers": {"q1": ["o2", "x" * 10_000]}}

        _, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [
                _requires(_call("nirai_ask_master", {
                    "question": "Choose",
                    "options": ["A", "B"],
                    "allow_multiple": True,
                }, "fc-question")),
                _completed("int-2"),
            ],
            approval=answer,
        )
        assert seen["questions"][0]["allow_free_text"] is True
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        answers = continuation["input"][0]["result"]["answers"]
        assert answers[0] == "B"
        assert len(answers[1]) == 4_000
        assert sum(len(value) for value in answers) <= 12_000

    asyncio.run(scenario())


def test_antigravity_plan_uses_common_plan_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))

        async def revise(request_id, kind, payload):
            assert kind == "plan"
            assert payload["approval_required"] is True
            return {"decision": "revise", "reason": "add tests"}

        _, calls, emitted = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [
                _requires(_call("nirai_submit_plan", {"plan": "1. edit\n2. test"}, "fc-plan")),
                _completed("int-2"),
            ],
            approval=revise,
        )
        assert any(kind == "plan" for kind, _ in emitted)
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["result"] == {"ok": False, "decision": "revise", "reason": "add tests"}

    asyncio.run(scenario())


def test_antigravity_plan_cancel_cancels_agent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))

        async def emit(*args):
            return None

        async def cancel_plan(*args):
            return {"decision": "cancel"}

        with pytest.raises(asyncio.CancelledError):
            await adapter._submit_plan(
                "fc-plan-cancel",
                {"plan": "stop here"},
                emit=emit,
                wait_for_master=cancel_plan,
            )

    asyncio.run(scenario())


def test_antigravity_large_diff_fails_closed_before_master(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = AntigravityAgentAdapter(_policy(tmp_path))
        content = "x\n" * (ANTIGRAVITY_DIFF_LIMIT + 100)
        _, calls, _ = await _run_with_fake_api(
            monkeypatch,
            adapter,
            _request(tmp_path),
            [
                _requires(_call("nirai_write_text_file", {"path": "huge.txt", "content": content})),
                _completed("int-2"),
            ],
        )
        continuation = next(payload for path, _, payload in calls if path == "/interactions" and payload and "previous_interaction_id" in payload)
        assert continuation["input"][0]["is_error"] is True
        assert "review limit" in continuation["input"][0]["result"]["error"]
        assert not (_request(tmp_path).working_dir / "huge.txt").exists()

    asyncio.run(scenario())


def test_antigravity_rejects_non_agent_gemini_model_and_reasoning_effort(tmp_path: Path) -> None:
    adapter = AntigravityAgentAdapter(_policy(tmp_path))

    async def emit(*args):
        return None

    async def wait(*args):
        return {}

    with pytest.raises(AgentRuntimeProtocolError, match="antigravity"):
        asyncio.run(adapter.run(_request(tmp_path, model="gemini-3.5-flash"), emit=emit, wait_for_master=wait))

    request = _request(tmp_path)
    request = AgentRunRequest(**{**request.__dict__, "reasoning_effort": "high"})
    with pytest.raises(AgentRuntimeProtocolError, match="reasoning_effort"):
        asyncio.run(adapter.run(request, emit=emit, wait_for_master=wait))


def test_default_manager_registers_gemini_agent_with_precise_capabilities(tmp_path: Path) -> None:
    manager = AgentRuntimeManager(_root(tmp_path), ("runtime\\workspace",))
    assert manager.supports_provider("gemini") is True
    assert manager.provider_capabilities("gemini") == frozenset({
        "approval",
        "question",
        "plan",
        "file_diff",
        "command_result",
    })
    assert manager.provider_capabilities("codex") >= {"approval", "todo", "artifact"}


def test_server_agent_work_is_model_aware_for_gemini(tmp_path: Path) -> None:
    from core.config import load_config
    from core.server import CoreServer

    root = _root(tmp_path)
    (root / "config.toml").write_text(
        """
[core]
port = 8765
log_level = "INFO"
[world]
fps = 30
audio_volume = 65
voicevox_url = "http://127.0.0.1:50021"
[ecomode]
resume_delay_sec = 10
[residents]
enabled = []
[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip(),
        encoding="utf-8",
    )
    server = CoreServer(load_config(root), port_override=0)
    assert server._provider_can_agent_work("gemini", "antigravity-preview-05-2026") is True
    assert server._provider_can_agent_work("gemini", "gemini-3.5-flash") is False
    assert server._provider_can_agent_work("gemini", None) is False
