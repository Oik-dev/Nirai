from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from core.agents import AgentRuntimeManager
from core.agents.base import AgentRunRequest, AgentRuntimeError
from core.agents.cursor_acp import (
    CursorAcpAdapter,
    _common_permission_options,
    _cursor_review_manifest,
    _permission_option_for_decision,
    _permission_reject_result,
    _resolve_select_value,
    _stop_process_tree,
)
from core.agents.cursor_events import (
    cursor_message_chunk_text,
    normalize_cursor_session_update,
    validate_cursor_tool_paths,
)
from core.agents.safety import AgentSafetyError, AgentWorkspacePolicy


def _policy(tmp_path: Path) -> AgentWorkspacePolicy:
    return AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))


def _request(tmp_path: Path) -> AgentRunRequest:
    working = tmp_path / "runtime" / "workspace" / "TASK-CURSOR"
    working.mkdir(parents=True, exist_ok=True)
    return AgentRunRequest(
        task_id="TASK-CURSOR",
        agent_session_id="AS-CURSOR",
        resident="Cursor",
        provider="cursor",
        prompt="create result.txt",
        working_dir=working,
        model="cursor-grok-4.6-high",
    )


def test_cursor_event_normalizer_drops_private_thought_and_aggregates_message_separately(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-EVENT")

    thought = {"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "private"}}
    message = {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "public"}}

    assert normalize_cursor_session_update(thought, working_dir=working, workspace_policy=policy) == []
    assert normalize_cursor_session_update(message, working_dir=working, workspace_policy=policy) == []
    assert cursor_message_chunk_text(thought) is None
    assert cursor_message_chunk_text(message) == "public"


def test_cursor_event_normalizer_maps_file_and_command_tools_inside_workspace(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-TOOLS")

    file_events = normalize_cursor_session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": "edit-1",
        "kind": "edit",
        "title": "Edit result.txt",
        "status": "in_progress",
        "locations": [{"path": "result.txt"}],
    }, working_dir=working, workspace_policy=policy)
    command_events = normalize_cursor_session_update({
        "sessionUpdate": "tool_call",
        "toolCallId": "cmd-1",
        "kind": "execute",
        "title": "Run tests",
        "rawInput": {"command": "python -m pytest"},
    }, working_dir=working, workspace_policy=policy)

    assert file_events[0][0] == "file_change"
    assert file_events[0][1]["operation_id"] == "edit-1"
    assert file_events[0][1]["changes"][0]["relative_path"] == "result.txt"
    assert command_events == [("command_execution", {
        "operation_id": "cmd-1",
        "phase": "started",
        "tool_type": "execute",
        "status": None,
        "title": "Run tests",
        "command": "python -m pytest",
        "cwd": str(working),
    })]


def test_cursor_tool_path_validation_fails_closed_outside_workspace(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    working = policy.resolve_working_dir(None, task_id="TASK-ESCAPE")
    outside = tmp_path / "outside.txt"

    with pytest.raises(AgentSafetyError):
        validate_cursor_tool_paths(
            {
                "kind": "edit",
                "toolCallId": "escape-1",
                "locations": [{"path": str(outside)}],
            },
            working_dir=working,
            workspace_policy=policy,
        )


def test_cursor_model_resolver_preserves_exact_acp_effort_and_refuses_silent_downgrade() -> None:
    config = {
        "id": "model",
        "category": "model",
        "options": [
            {"value": "default[]", "name": "Auto"},
            {"value": "grok-4.6[effort=high,fast=true]", "name": "grok-4.6"},
            {"value": "gpt-5.6-sol[context=272k,reasoning=medium,fast=false]", "name": "gpt-5.6-sol"},
            {"value": "claude-fable-5-1[thinking=true,context=300k,effort=high]", "name": "claude-fable-5-1"},
        ],
    }

    assert _resolve_select_value(config, "auto") == "default[]"
    assert _resolve_select_value(config, "cursor-grok-4.6-high-fast") == "grok-4.6[effort=high,fast=true]"
    assert _resolve_select_value(config, "cursor-grok-4.6-high") is None
    assert _resolve_select_value(config, "cursor-grok-4.6-xhigh") is None
    assert _resolve_select_value(config, "gpt-5.6-sol-medium") == "gpt-5.6-sol[context=272k,reasoning=medium,fast=false]"
    assert _resolve_select_value(config, "gpt-5.6-sol-high") is None
    assert _resolve_select_value(config, "claude-fable-5-1-thinking-high") == "claude-fable-5-1[thinking=true,context=300k,effort=high]"
    assert _resolve_select_value(config, "missing-model") is None


def test_cursor_agent_home_copies_only_auth_state_and_writes_nirai_safety_config(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text('{"opaque":"auth"}\n', encoding="utf-8")
    (source / "cli-config.json").write_text('{"permissions":{"allow":["Shell(*)"]}}\n', encoding="utf-8")
    (source / "skills-cursor").mkdir()
    (source / "skills-cursor" / "unsafe.md").write_text("do not copy", encoding="utf-8")
    (source / "projects").mkdir()

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-ISOLATED")
    try:
        config_dir = home / ".cursor"
        assert (config_dir / "agent-cli-state.json").read_text(encoding="utf-8") == '{"opaque":"auth"}\n'
        assert not (config_dir / "skills-cursor").exists()
        assert not (config_dir / "projects").exists()
        config = json.loads((config_dir / "cli-config.json").read_text(encoding="utf-8"))
        assert config["approvalMode"] == "allowlist"
        assert config["permissions"]["allow"] == []
        assert "WebFetch(*)" in config["permissions"]["deny"]
        assert "Mcp(*:*)" in config["permissions"]["deny"]
        assert any(item.startswith("Read(") and "Users" in item for item in config["permissions"]["deny"])
        assert any(item.startswith("Write(") and "/core/**" in item for item in config["permissions"]["deny"])
        assert config["display"]["showThinkingBlocks"] is False
    finally:
        adapter._cleanup_cursor_home(home)
    assert not home.exists()


def test_cursor_agent_home_cleans_stale_credential_homes_before_new_session(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    stale = tmp_path / "runtime" / "cursor_agent_homes" / "AS-STALE"
    stale.mkdir(parents=True)
    (stale / "credential-copy.txt").write_text("stale", encoding="utf-8")

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-NEW")
    try:
        assert not stale.exists()
        assert home.exists()
    finally:
        adapter._cleanup_cursor_home(home)


def test_cursor_agent_environment_does_not_forward_unrelated_secrets(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("NIRAI_SECRET_SENTINEL", "must-not-leak")
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\tester\AppData\Local")
    monkeypatch.setenv("APPDATA", r"C:\Users\tester\AppData\Roaming")
    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-ENV")
    try:
        env = adapter._build_cursor_environment(home)
        assert env["PATH"] == "safe-path"
        assert "NIRAI_SECRET_SENTINEL" not in env
        assert "GEMINI_API_KEY" not in env
        assert env["USERPROFILE"] == str(home)
        assert env["HOME"] == str(home)
        assert env["CURSOR_CONFIG_DIR"] == str(home / ".cursor")
        assert env["LOCALAPPDATA"] == r"C:\Users\tester\AppData\Local"
        assert env["APPDATA"] == r"C:\Users\tester\AppData\Roaming"
        assert env["TEMP"].startswith(str(home))
    finally:
        adapter._cleanup_cursor_home(home)


def test_cursor_review_manifest_keeps_every_path_visible_when_diffs_are_too_large() -> None:
    changes = [
        {
            "path": f"D:/workspace/file-{index}.txt",
            "relative_path": f"file-{index}.txt",
            "change_type": "modify",
            "diff": "x" * 12_000,
        }
        for index in range(10)
    ]

    review = _cursor_review_manifest(changes)
    assert len(review) == len(changes)
    assert [item["relative_path"] for item in review] == [item["relative_path"] for item in changes]
    assert all("diff" not in item for item in review)


def test_cursor_review_manifest_rejects_path_manifest_that_cannot_fit_safely() -> None:
    changes = [
        {
            "path": "D:/workspace/" + ("x" * 700) + f"-{index}.txt",
            "relative_path": ("x" * 700) + f"-{index}.txt",
            "change_type": "modify",
        }
        for index in range(50)
    ]

    with pytest.raises(AgentRuntimeError, match="manifest is too large"):
        _cursor_review_manifest(changes)


def test_cursor_staging_requires_master_approval_before_real_workspace_changes(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("staged\n", encoding="utf-8")
            emitted: list[tuple[str, dict[str, Any]]] = []

            async def emit(event_type, payload):
                emitted.append((event_type, payload))

            async def approve(request_id, kind, payload):
                assert kind == "approval"
                assert payload["kind"] == "file_change"
                assert payload["options"] == ["approve_once", "reject", "cancel"]
                assert not (request.working_dir / "result.txt").exists()
                return {"decision": "approve_once"}

            await adapter._review_and_apply_staged_changes(
                request,
                staging_dir=staging,
                review_dir=tmp_path / "cursor-review-approved",
                baseline=baseline,
                emit=emit,
                wait_for_master=approve,
            )
            assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "staged\n"
            assert [kind for kind, _ in emitted] == ["file_change", "approval_request", "file_change"]
            operation_id = emitted[0][1]["operation_id"]
            assert emitted[1][1]["operation_id"] == operation_id
            assert emitted[2][1]["status"] == "completed"
            assert emitted[0][1]["changes"][0]["relative_path"] == "result.txt"
            assert "+++ b/result.txt" in emitted[0][1]["changes"][0]["diff"]
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_approved_apply_never_recreates_deleted_external_workspace_root(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        policy = AgentWorkspacePolicy(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = CursorAcpAdapter(policy)
        request = AgentRunRequest(
            task_id="TASK-CURSOR-EXTERNAL",
            agent_session_id="AS-CURSOR-EXTERNAL",
            resident="Cursor",
            provider="cursor",
            prompt="create result.txt",
            working_dir=project,
            model="cursor-grok-4.6-high",
        )
        staging, baseline = adapter._prepare_staging_workspace(
            request.agent_session_id,
            request.working_dir,
        )
        try:
            (staging / "result.txt").write_text("staged\n", encoding="utf-8")

            async def emit(*args):
                return None

            async def approve_then_delete_workspace(*args):
                project.rmdir()
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="was rolled back"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-deleted-root",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve_then_delete_workspace,
                )
            assert project.exists() is False
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_refuses_apply_if_staging_changes_during_master_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("reviewed\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def approve_after_mutation(request_id, kind, payload):
                assert kind == "approval"
                (staging / "result.txt").write_text("tampered-after-review\n", encoding="utf-8")
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="staging workspace changed after review"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-mutated-staging",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve_after_mutation,
                )
            assert not (request.working_dir / "result.txt").exists()
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_reject_leaves_real_workspace_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("rejected\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def reject(request_id, kind, payload):
                return {"decision": "reject"}

            with pytest.raises(AgentRuntimeError, match="rejected Cursor staged file changes"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-rejected",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=reject,
                )
            assert not (request.working_dir / "result.txt").exists()
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_refuses_apply_if_real_workspace_changed_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("modify seed.txt\n", encoding="utf-8")
        (request.working_dir / "seed.txt").write_text("before\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "seed.txt").write_text("cursor\n", encoding="utf-8")
            (request.working_dir / "seed.txt").write_text("master\n", encoding="utf-8")

            async def emit(event_type, payload):
                return None

            async def approve(request_id, kind, payload):
                return {"decision": "approve_once"}

            with pytest.raises(AgentRuntimeError, match="workspace changed while Cursor was working"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-conflict",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=approve,
                )
            assert (request.working_dir / "seed.txt").read_text(encoding="utf-8") == "master\n"
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_apply_failure_rolls_back_already_applied_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    request = _request(tmp_path)
    (request.working_dir / "task.md").write_text("modify files\n", encoding="utf-8")
    (request.working_dir / "a.txt").write_text("a-before\n", encoding="utf-8")
    (request.working_dir / "b.txt").write_text("b-before\n", encoding="utf-8")
    staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
    try:
        (staging / "a.txt").write_text("a-after\n", encoding="utf-8")
        (staging / "b.txt").write_text("b-after\n", encoding="utf-8")
        changes = adapter._collect_staged_changes(request.working_dir, staging, baseline)
        original_copy = adapter._atomic_copy_file
        failed_once = False

        def fail_second_staged_copy(source: Path, target: Path) -> None:
            nonlocal failed_once
            if source == staging / "b.txt" and not failed_once:
                failed_once = True
                raise OSError("simulated apply failure")
            original_copy(source, target)

        monkeypatch.setattr(adapter, "_atomic_copy_file", fail_second_staged_copy)
        with pytest.raises(AgentRuntimeError, match="was rolled back"):
            adapter._apply_staged_changes(request.working_dir, staging, baseline, changes)

        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "a-before\n"
        assert (request.working_dir / "b.txt").read_text(encoding="utf-8") == "b-before\n"
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_staging_never_applies_task_metadata_changes(tmp_path: Path) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    request = _request(tmp_path)
    (request.working_dir / "task.md").write_text("original\n", encoding="utf-8")
    staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
    try:
        (staging / "task.md").write_text("tampered\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="protected Task metadata"):
            adapter._collect_staged_changes(request.working_dir, staging, baseline)
        assert (request.working_dir / "task.md").read_text(encoding="utf-8") == "original\n"
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_permission_bridge_maps_master_decision_and_blocks_external_tools(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        source.mkdir(parents=True)
        (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []
        waited: list[tuple[str, str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def wait_for_master(request_id, kind, payload):
            waited.append((request_id, kind, payload))
            return {"decision": "approve_once"}

        result = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "edit-1",
                    "kind": "edit",
                    "title": "Edit result.txt",
                    "locations": [{"path": "result.txt"}],
                },
                "options": [
                    {"optionId": "allow-once"},
                    {"optionId": "allow-always"},
                    {"optionId": "reject-once"},
                ],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert result == {"outcome": {"outcome": "selected", "optionId": "allow-once"}}
        assert waited[0][0:2] == ("edit-1", "approval")
        assert [kind for kind, _ in emitted[:2]] == ["file_change", "approval_request"]
        assert emitted[0][1]["operation_id"] == "edit-1"
        assert emitted[0][1]["changes"][0]["relative_path"] == "result.txt"
        assert emitted[1][1]["kind"] == "file_change"
        assert emitted[1][1]["operation_id"] == "edit-1"

        waited.clear()
        external = await adapter._handle_permission_request(
            {
                "toolCall": {"toolCallId": "search-1", "kind": "search", "title": "Web search: current docs"},
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert external == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert waited == []
        assert any(kind == "status_message" and payload.get("kind") == "external_tool_blocked" for kind, payload in emitted)

    asyncio.run(scenario())


def test_cursor_permission_reject_never_falls_back_to_allow_only_option(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def should_not_wait(request_id, kind, payload):
            raise AssertionError("baseline deny must not reach Master approval")

        allow_only = [{"optionId": "allow-once"}]
        external = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "web-allow-only",
                    "kind": "search",
                    "title": "Web search",
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=should_not_wait,
        )
        assert external == {"outcome": {"outcome": "cancelled"}}

        unknown_path = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "edit-allow-only",
                    "kind": "edit",
                    "title": "Edit unknown file",
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=should_not_wait,
        )
        assert unknown_path == {"outcome": {"outcome": "cancelled"}}

        async def reject_master(request_id, kind, payload):
            return {"decision": "reject"}

        rejected = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-reject-allow-only",
                    "kind": "execute",
                    "title": "Run command",
                    "rawInput": {"command": "echo no"},
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=reject_master,
        )
        assert rejected == {"outcome": {"outcome": "cancelled"}}

        async def cancel_master(request_id, kind, payload):
            return {"decision": "cancel"}

        cancelled = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-cancel-allow-only",
                    "kind": "execute",
                    "title": "Run command",
                    "rawInput": {"command": "echo no"},
                },
                "options": allow_only,
            },
            request=request,
            emit=emit,
            wait_for_master=cancel_master,
        )
        assert cancelled == {"outcome": {"outcome": "cancelled"}}

    asyncio.run(scenario())


def test_cursor_permission_semantics_use_option_kind_not_option_id() -> None:
    options = [
        {"optionId": "allow", "kind": "allow_once"},
        {"optionId": "deny", "kind": "reject_once"},
    ]

    assert _common_permission_options(options) == ["approve_once", "reject", "cancel"]
    assert _permission_option_for_decision(options, "approve_once") == "allow"
    assert _permission_option_for_decision(options, "reject") == "deny"
    assert _permission_option_for_decision(options, "cancel") == "deny"
    assert _permission_reject_result(options) == {
        "outcome": {"outcome": "selected", "optionId": "deny"}
    }


def test_cursor_permission_bridge_rejects_outside_file_before_master_ui(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        emitted: list[tuple[str, dict[str, Any]]] = []
        waited = False

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def wait_for_master(request_id, kind, payload):
            nonlocal waited
            waited = True
            return {"decision": "approve_once"}

        response = await adapter._handle_permission_request(
            {
                "toolCall": {
                    "toolCallId": "escape-approval",
                    "kind": "edit",
                    "title": "Edit outside",
                    "locations": [{"path": str(tmp_path / "outside.txt")}],
                },
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert response == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert waited is False
        assert any(kind == "error" and payload.get("code") == "cursor_tool_outside_workspace" for kind, payload in emitted)
        assert all(kind != "approval_request" for kind, _ in emitted)

    asyncio.run(scenario())


def test_cursor_question_and_plan_extensions_bridge_existing_master_contract(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def answer_question(request_id, kind, payload):
            assert request_id == "q-call"
            assert kind == "question"
            assert payload["questions"][0]["options"][0] == {"id": "agent", "label": "Agent"}
            assert payload["questions"][0]["allow_free_text"] is False
            assert payload["questions"][0]["allow_multiple"] is False
            return {"answers": {"q1": ["Agent"]}}

        question_result = await adapter._handle_question_request(
            {
                "toolCallId": "q-call",
                "title": "Choose mode",
                "questions": [{
                    "id": "q1",
                    "prompt": "Which mode?",
                    "options": [{"id": "agent", "label": "Agent"}, {"id": "plan", "label": "Plan"}],
                    "allowMultiple": False,
                }],
            },
            emit=emit,
            wait_for_master=answer_question,
        )
        assert question_result == {
            "outcome": {
                "outcome": "answered",
                "answers": [{"questionId": "q1", "selectedOptionIds": ["agent"]}],
            }
        }

        async def approve_plan(request_id, kind, payload):
            assert request_id == "plan-call"
            assert kind == "plan"
            assert payload["markdown"] == "1. Edit\n2. Test"
            assert payload["steps"][0]["step"] == "Edit"
            return {"decision": "approve"}

        plan_result = await adapter._handle_plan_request(
            {
                "toolCallId": "plan-call",
                "name": "Small plan",
                "plan": "1. Edit\n2. Test",
                "todos": [{"id": "1", "content": "Edit", "status": "pending"}],
            },
            emit=emit,
            wait_for_master=approve_plan,
        )
        assert plan_result == {"outcome": {"outcome": "accepted"}}
        assert [kind for kind, _ in emitted] == ["question_request", "plan"]

    asyncio.run(scenario())


def test_cursor_process_tree_stop_attempts_taskkill_even_if_parent_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "nt":
        pytest.skip("Cursor Agent Runtime process-tree contract is Windows-specific")

    async def scenario() -> None:
        calls: list[tuple[object, ...]] = []

        class FakeKiller:
            returncode = 0

            async def wait(self):
                return 0

            def kill(self):
                self.returncode = -9

        class FakeProcess:
            pid = 424242
            returncode = 0

            async def wait(self):
                return self.returncode

            def terminate(self):
                raise AssertionError("already exited parent must not need terminate")

            def kill(self):
                raise AssertionError("already exited parent must not need kill")

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(args)
            return FakeKiller()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
        assert await _stop_process_tree(FakeProcess()) is True
        assert calls
        assert calls[0][0:4] == ("taskkill.exe", "/PID", "424242", "/T")

    asyncio.run(scenario())


def test_default_agent_runtime_advertises_cursor_agent_work(tmp_path: Path) -> None:
    manager = AgentRuntimeManager(tmp_path, ("runtime\\workspace",))
    assert manager.supports_provider("codex") is True
    assert manager.supports_provider("cursor") is True
    assert manager.supports_provider("gemini") is True
    assert manager.provider_capabilities("cursor") == frozenset({
        "approval",
        "question",
        "plan",
        "todo",
        "subagent",
        "file_diff",
        "command_result",
    })
    assert "artifact" not in manager.provider_capabilities("cursor")
