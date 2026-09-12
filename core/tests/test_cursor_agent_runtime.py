from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any

import pytest

import core.agents.cursor_acp as cursor_acp_module
from core.agents import AgentRuntimeManager
from core.agents.base import AgentProviderLimitError, AgentRunRequest, AgentRunResult, AgentRuntimeError
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
from core.agents.cursor_workspace import StagedApplyCancelledAfterCommit
from core.agents.safety import AgentSafetyError, AgentWorkspacePolicy
from core.brains.process_manager import CompletedInvocation


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


def test_provider_limit_partial_work_is_preserved_without_applying_staging(tmp_path: Path) -> None:
    policy = _policy(tmp_path)
    adapter = CursorAcpAdapter(policy)
    request = _request(tmp_path)
    (request.working_dir / "keep.txt").write_text("original\n", encoding="utf-8")
    (request.working_dir / "delete.txt").write_text("delete me\n", encoding="utf-8")
    ignore_parts = adapter._workspace_ignore_parts(request.working_dir, frozenset({".git", ".cursor"}))
    staging, baseline = adapter._prepare_staging_workspace(
        request.agent_session_id,
        request.working_dir,
        ignore_parts=ignore_parts,
    )
    try:
        (staging / "keep.txt").write_text("partial edit\n", encoding="utf-8")
        (staging / "new.txt").write_text("partial create\n", encoding="utf-8")
        (staging / "delete.txt").unlink()

        preserved = adapter._preserve_partial_staged_changes(
            request,
            working_dir=request.working_dir,
            staging_dir=staging,
            baseline=baseline,
            ignore_parts=ignore_parts,
            reason="provider_quota_exhausted",
        )

        assert preserved is not None
        partial_root = Path(preserved)
        manifest = json.loads((partial_root / "recovery.json").read_text(encoding="utf-8"))
        assert manifest["state"] == "provider_limit_interrupted"
        assert manifest["reason"] == "provider_quota_exhausted"
        assert {item["relative_path"] for item in manifest["changes"]} == {
            "delete.txt", "keep.txt", "new.txt"
        }
        assert (partial_root / "files" / "keep.txt").read_text(encoding="utf-8") == "partial edit\n"
        assert (partial_root / "files" / "new.txt").read_text(encoding="utf-8") == "partial create\n"
        assert not (partial_root / "files" / "delete.txt").exists()
        assert (request.working_dir / "keep.txt").read_text(encoding="utf-8") == "original\n"
        assert (request.working_dir / "delete.txt").is_file()
        assert not (request.working_dir / "new.txt").exists()
    finally:
        adapter._cleanup_staging_workspace(staging)


@pytest.mark.parametrize("result_format", ["nonzero", "json_error"])
def test_cursor_exact_cli_preserves_quota_failure_in_either_result_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result_format: str,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(cursor_acp_module, "resolve_cursor_command", lambda: ("node.exe", "fake.js"))
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "model": "cursor-grok-4.6-xhigh"})

        class FakeCli:
            async def run(self, _invocation_id, _argv, *, cwd, **_kwargs):
                (cwd / "partial.txt").write_text("recoverable edit", encoding="utf-8")
                if result_format == "nonzero":
                    return CompletedInvocation(1, "", "usage limit reached")
                return CompletedInvocation(0, json.dumps({
                    "is_error": True, "error": {"code": "quota_exhausted"},
                }), "")

        adapter._cli_process_manager = FakeCli()  # type: ignore[assignment]

        async def emit(*_args):
            pass

        with pytest.raises(AgentProviderLimitError) as caught:
            await adapter.run(request, emit=emit, wait_for_master=emit)
        assert caught.value.partial_work_path is not None
        assert (Path(caught.value.partial_work_path) / "files" / "partial.txt").read_text() == "recoverable edit"
        assert not (request.working_dir / "partial.txt").exists()

    asyncio.run(scenario())


def test_quota_preservation_finishes_before_cancel_cleans_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(cursor_acp_module, "resolve_cursor_command", lambda: ("node.exe", "fake.js"))
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "model": "cursor-grok-4.6-xhigh"})
        started = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        preserve = adapter._preserve_partial_staged_changes
        stage_files: list[Path] = []

        def slow_preserve(*args, **kwargs):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return preserve(*args, **kwargs)

        monkeypatch.setattr(adapter, "_preserve_partial_staged_changes", slow_preserve)

        class FakeCli:
            async def run(self, _invocation_id, _argv, *, cwd, **_kwargs):
                source = cwd / "partial.txt"
                source.write_text("recoverable edit", encoding="utf-8")
                stage_files.append(source)
                return CompletedInvocation(1, "", "usage limit reached")

        adapter._cli_process_manager = FakeCli()  # type: ignore[assignment]

        async def emit(*_args):
            pass

        task = asyncio.create_task(adapter.run(request, emit=emit, wait_for_master=emit))
        try:
            await asyncio.wait_for(started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.sleep(0.05)
            assert not task.done(), "cleanup must wait for the file worker, including repeated cancellation"
            assert stage_files[0].is_file()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
        partial = tmp_path / "runtime" / "agent_sessions" / request.agent_session_id / "partial_work"
        assert (partial / "files" / "partial.txt").read_text() == "recoverable edit"

    asyncio.run(scenario())


def test_cursor_nirai_read_only_staging_excludes_secret_and_private_asset_roots(tmp_path: Path) -> None:
    root = tmp_path
    (root / "runtime" / "workspace").mkdir(parents=True)
    (root / "world" / "out").mkdir(parents=True)
    (root / "world" / ".env").write_text("SECRET=do-not-stage\n", encoding="utf-8")
    (root / "world" / ".env.local").write_text("SECRET_LOCAL=do-not-stage\n", encoding="utf-8")
    (root / "world" / "visible.ts").write_text("export const visible = true\n", encoding="utf-8")
    (root / "world" / "out" / "bundle.js").write_text("generated\n", encoding="utf-8")
    for private_root in (".tools", ".vrm", ".vrma"):
        path = root / private_root
        path.mkdir()
        (path / "private.bin").write_bytes(b"do-not-stage")
    policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
    adapter = CursorAcpAdapter(policy)

    ignored = adapter._read_only_staging_ignore_parts(root)
    staging, baseline = adapter._prepare_staging_workspace(
        "AS-SECRET-REVIEW",
        root,
        ignore_parts=ignored,
    )
    try:
        assert "world/.env" not in baseline
        assert "world/.env.local" not in baseline
        assert "world/out/bundle.js" not in baseline
        assert ".tools/private.bin" not in baseline
        assert ".vrm/private.bin" not in baseline
        assert ".vrma/private.bin" not in baseline
        assert "world/visible.ts" in baseline
        assert not (staging / "world" / ".env").exists()
        assert not (staging / "world" / ".env.local").exists()
        assert not (staging / "world" / "out").exists()
        assert not (staging / ".tools").exists()
        assert not (staging / ".vrm").exists()
        assert not (staging / ".vrma").exists()
        assert (staging / "world" / "visible.ts").is_file()
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_nirai_integrated_audit_staging_excludes_runtime_credentials_and_assets(tmp_path: Path) -> None:
    root = tmp_path
    (root / "runtime" / "workspace").mkdir(parents=True)
    (root / "core").mkdir()
    (root / "core" / "visible.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=do-not-stage\n", encoding="utf-8")
    (root / "runtime" / "state.json").write_text("{}\n", encoding="utf-8")
    (root / "avatars").mkdir()
    (root / "avatars" / "private.vrm").write_bytes(b"private")
    policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
    adapter = CursorAcpAdapter(policy)

    working, ignored = adapter._resolve_run_workspace(AgentRunRequest(
        task_id="IA-NIRAI-STAGE",
        agent_session_id="AS-IA-NIRAI-STAGE",
        resident="Astra",
        provider="cursor",
        prompt="integrated audit",
        working_dir=root,
        purpose="integrated_audit",
    ))
    assert working == root.resolve()
    staging, baseline = adapter._prepare_staging_workspace(
        "AS-IA-NIRAI-STAGE",
        working,
        ignore_parts=ignored,
    )
    try:
        assert "core/visible.py" in baseline
        assert ".env" not in baseline
        assert not any(path.startswith("runtime/") for path in baseline)
        assert not any(path.startswith("avatars/") for path in baseline)
        assert (staging / "core" / "visible.py").is_file()
        assert not (staging / ".env").exists()
        assert not (staging / "runtime").exists()
        assert not (staging / "avatars").exists()
    finally:
        adapter._cleanup_staging_workspace(staging)


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
        assert "WebSearch(*)" in config["permissions"]["deny"]
        assert "Browser(*)" in config["permissions"]["deny"]
        assert "Computer(*)" in config["permissions"]["deny"]
        assert "Mcp(*:*)" in config["permissions"]["deny"]
        assert "Shell(*)" in config["permissions"]["deny"]
        assert any(item.startswith("Read(") and "Users" in item for item in config["permissions"]["deny"])
        assert any(item.startswith("Write(") and "/core/**" in item for item in config["permissions"]["deny"])
        assert config["display"]["showThinkingBlocks"] is False
    finally:
        adapter._cleanup_cursor_home(home)
    assert not home.exists()


def test_cursor_nirai_root_review_stages_outside_repo_and_denies_real_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        source.mkdir(parents=True)
        (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        (tmp_path / "core").mkdir()
        (tmp_path / "world").mkdir()
        (tmp_path / "core" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        policy = _policy(tmp_path)
        adapter = CursorAcpAdapter(policy)
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = AgentRunRequest(
            task_id="HR-ROOT-XHIGH",
            agent_session_id="AS-ROOT-XHIGH",
            resident="Holo",
            provider="cursor",
            prompt="Review Nirai root",
            working_dir=tmp_path.resolve(),
            model="cursor-grok-4.6-xhigh",
            read_only=True,
            purpose="review",
        )
        calls: list[dict[str, Any]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                config = json.loads(
                    (Path(env["CURSOR_CONFIG_DIR"]) / "cli-config.json").read_text(encoding="utf-8")
                )
                calls.append({"cwd": Path(cwd).resolve(), "config": config, "argv": tuple(argv)})
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "SAFE\nNo blocking finding.",
                        "session_id": "cursor-root-review-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]

        async def emit(_event_type, _payload):
            return None

        async def no_master(*_args):
            raise AssertionError("read-only root review must not ask Master")

        summary = await adapter.run(request, emit=emit, wait_for_master=no_master)
        assert summary.startswith("SAFE")
        assert len(calls) == 1
        staging = calls[0]["cwd"]
        with pytest.raises(ValueError):
            staging.relative_to(tmp_path.resolve())
        root_pattern = tmp_path.resolve().as_posix().rstrip("/") + "/**"
        deny = calls[0]["config"]["permissions"]["deny"]
        assert f"Read({root_pattern})" in deny
        assert f"Write({root_pattern})" in deny
        assert calls[0]["argv"][calls[0]["argv"].index("--mode") + 1] == "ask"

    asyncio.run(scenario())


def test_cursor_read_only_acp_configures_ask_mode() -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter.__new__(CursorAcpAdapter)
        calls: list[tuple[str, dict[str, Any]]] = []

        class FakeClient:
            async def request(self, method: str, params: dict[str, Any]):
                calls.append((method, params))
                return {}

        await adapter._configure_session(
            FakeClient(),
            "cursor-session-1",
            [{
                "id": "mode",
                "category": "mode",
                "options": [
                    {"value": "ask", "name": "Ask"},
                    {"value": "agent", "name": "Agent"},
                ],
            }],
            requested_model=None,
            requested_reasoning=None,
            read_only=True,
        )

        assert calls[0] == (
            "session/set_config_option",
            {"sessionId": "cursor-session-1", "configId": "mode", "value": "ask"},
        )

    asyncio.run(scenario())


def test_cursor_agent_home_cleans_stale_credential_homes_before_new_session(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    stale = tmp_path / "runtime" / "cursor_agent_homes" / "AS-STALE"
    stale.mkdir(parents=True)
    (stale / "credential-copy.txt").write_text("stale", encoding="utf-8")
    os.utime(stale, (1, 1))

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-NEW")
    try:
        assert not stale.exists()
        assert home.exists()
    finally:
        adapter._release_runtime_id("AS-NEW")
        adapter._cleanup_cursor_home(home)


def test_cursor_discard_conversation_context_removes_stable_staging(tmp_path: Path) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    conversation_id = "CV-STAGING-DISCARD"
    working = tmp_path / "project"
    working.mkdir()
    (working / "module.py").write_text("VALUE = 1\n", encoding="utf-8")

    staging, _baseline = adapter._prepare_staging_workspace(
        "AS-CONVERSATION-DISCARD",
        working,
        ignore_parts=adapter._read_only_staging_ignore_parts(working),
        stable_key=conversation_id,
    )
    adapter._release_runtime_id("AS-CONVERSATION-DISCARD")
    try:
        assert staging.exists()
        adapter.discard_conversation_context(conversation_id)
        assert not staging.exists()
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_stale_cleanup_reaps_abandoned_conversation_staging(tmp_path: Path) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    staging_root = adapter.workspace_policy.default_workspace_root
    staging_root.mkdir(parents=True)
    abandoned = staging_root / ".cursor-conversation-abandoned"
    abandoned.mkdir()
    (abandoned / "secret-copy.txt").write_text("stale", encoding="utf-8")
    os.utime(abandoned, (1, 1))

    adapter._cleanup_stale_staging_workspaces(staging_root)

    assert not abandoned.exists()


def test_cursor_stale_cleanup_keeps_owned_conversation_staging(tmp_path: Path) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    staging_root = adapter.workspace_policy.default_workspace_root
    staging_root.mkdir(parents=True)
    active = staging_root / ".cursor-conversation-active"
    active.mkdir()
    (active / "module.py").write_text("live", encoding="utf-8")
    os.utime(active, (1, 1))
    adapter._claim_runtime_id(active.name)
    try:
        adapter._cleanup_stale_staging_workspaces(staging_root)
        assert active.exists()
    finally:
        adapter._release_runtime_id(active.name)
        adapter._cleanup_staging_workspace(active)


def test_cursor_agent_home_does_not_reap_young_unowned_home(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True)
    (source / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
    young = tmp_path / "runtime" / "cursor_agent_homes" / "AS-OTHER-CORE"
    young.mkdir(parents=True)
    (young / "credential-copy.txt").write_text("live", encoding="utf-8")

    adapter = CursorAcpAdapter(_policy(tmp_path))
    home = adapter._prepare_cursor_home("AS-NEW")
    try:
        assert young.exists()
        assert home.exists()
    finally:
        adapter._release_runtime_id("AS-NEW")
        adapter._cleanup_cursor_home(home)
        adapter._cleanup_cursor_home(young)


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


def test_cursor_read_only_review_permission_policy_allows_only_local_read_once(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "read_only": True})
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        options = [
            {"id": "allow-read", "kind": "allow_once"},
            {"id": "reject-tool", "kind": "reject_once"},
        ]
        read_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "read-1",
                    "kind": "read",
                    "title": "Read module.py",
                    "locations": [{"path": "module.py"}],
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        command_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "cmd-1",
                    "kind": "execute",
                    "title": "Run tests",
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        write_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "write-1",
                    "kind": "edit",
                    "title": "Edit module.py",
                    "locations": [{"path": "module.py"}],
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        browser_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "browser-1",
                    "kind": "browser",
                    "title": "Open browser",
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )
        computer_result = await adapter._handle_read_only_permission_request(
            {
                "toolCall": {
                    "toolCallId": "computer-1",
                    "kind": "computer",
                    "title": "Use computer",
                },
                "options": options,
            },
            request=request,
            emit=emit,
        )

        assert read_result == {"outcome": {"outcome": "selected", "optionId": "allow-read"}}
        assert command_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert write_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert browser_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert computer_result == {"outcome": {"outcome": "selected", "optionId": "reject-tool"}}
        assert any(payload.get("kind") == "cursor_review_read_allowed" for _, payload in emitted)
        assert sum(payload.get("kind") == "cursor_review_tool_rejected" for _, payload in emitted) == 4

    asyncio.run(scenario())


def test_cursor_read_only_workspace_walk_prunes_ignored_directories_before_descent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "review-root"
    root.mkdir()
    visited_after_root: list[str] = []

    def fake_walk(path, *, topdown, followlinks):
        assert Path(path) == root.resolve()
        assert topdown is True
        assert followlinks is False
        dirnames = ["runtime", "src", "NODE_MODULES"]
        yield str(root), dirnames, ["README.md"]
        visited_after_root.extend(dirnames)
        if "runtime" in dirnames or "NODE_MODULES" in dirnames:
            raise AssertionError("ignored directory was not pruned before descent")
        yield str(root / "src"), [], ["main.py"]

    monkeypatch.setattr(cursor_acp_module.os, "walk", fake_walk)
    files = list(CursorAcpAdapter._iter_workspace_files(
        root,
        ignore_parts=frozenset({"runtime", "node_modules"}),
    ))

    assert visited_after_root == ["src"]
    assert [path.relative_to(root).as_posix() for path in files] == ["README.md", "src/main.py"]


def test_cursor_staging_builds_baseline_while_copying_without_full_source_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    (project / "a.txt").write_text("alpha\n", encoding="utf-8")
    (project / "b.txt").write_text("beta\n", encoding="utf-8")
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\ProjectA"),
    )
    adapter = CursorAcpAdapter(policy)

    def fail_source_snapshot(*_args, **_kwargs):
        raise AssertionError("staging prepare must not hash the full source tree before copying")

    monkeypatch.setattr(adapter, "_workspace_snapshot", fail_source_snapshot)
    staging, baseline = adapter._prepare_staging_workspace(
        "AS-SINGLE-PASS-STAGE",
        project,
        ignore_parts=cursor_acp_module.CURSOR_WRITABLE_IGNORE_NAMES,
    )
    try:
        assert set(baseline) == {"a.txt", "b.txt"}
        assert (staging / "a.txt").read_text(encoding="utf-8") == "alpha\n"
        assert (staging / "b.txt").read_text(encoding="utf-8") == "beta\n"
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_writable_staging_excludes_dependency_and_generated_trees(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    (project / "src").mkdir(parents=True)
    (project / "node_modules" / "dep").mkdir(parents=True)
    (project / ".venv" / "Lib").mkdir(parents=True)
    (project / "build").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (project / "node_modules" / "dep" / "index.js").write_text("// dependency\n", encoding="utf-8")
    (project / ".venv" / "Lib" / "site.py").write_text("# generated\n", encoding="utf-8")
    (project / "build" / "artifact.bin").write_bytes(b"generated")

    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace", "projects\\ProjectA"))
    adapter = CursorAcpAdapter(policy)
    staging, _ = adapter._prepare_staging_workspace(
        "AS-WRITABLE-IGNORE",
        project,
        ignore_parts=cursor_acp_module.CURSOR_WRITABLE_IGNORE_NAMES,
    )
    try:
        assert (staging / "src" / "main.py").is_file()
        assert not (staging / "node_modules").exists()
        assert not (staging / ".venv").exists()
        assert not (staging / "build").exists()
    finally:
        adapter._preparing_ids.discard("AS-WRITABLE-IGNORE")
        adapter._cleanup_staging_workspace(staging)


def test_cursor_writable_staging_merges_workspace_niraiignore(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    (project / "src").mkdir(parents=True)
    (project / "large_export").mkdir(parents=True)
    (project / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (project / "large_export" / "generated.usda").write_bytes(b"generated")
    (project / "scratch.tmp").write_text("temporary\n", encoding="utf-8")
    (project / ".niraiignore").write_text("large_export\n*.tmp\n", encoding="utf-8")

    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace", "projects\\ProjectA"))
    adapter = CursorAcpAdapter(policy)
    request = AgentRunRequest(
        task_id="TASK-CURSOR-NIRAIIGNORE",
        agent_session_id="AS-CURSOR-NIRAIIGNORE",
        resident="Cursor",
        provider="cursor",
        prompt="inspect the project",
        working_dir=project,
        model="cursor-grok-4.6-high",
    )
    working_dir, ignore_parts = adapter._resolve_run_workspace(request)
    staging, baseline = adapter._prepare_staging_workspace(
        request.agent_session_id,
        working_dir,
        ignore_parts=ignore_parts,
    )
    try:
        assert "src/main.py" in baseline
        assert ".niraiignore" in baseline
        assert "large_export/generated.usda" not in baseline
        assert "scratch.tmp" not in baseline
        assert not (staging / "large_export").exists()
        assert not (staging / "scratch.tmp").exists()
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_writable_apply_uses_same_ignore_set_as_staging_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        project = tmp_path / "projects" / "ProjectA"
        (project / "src").mkdir(parents=True)
        (project / "node_modules" / "dep").mkdir(parents=True)
        (project / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
        (project / "node_modules" / "dep" / "index.js").write_text(
            "dependency\n",
            encoding="utf-8",
        )
        policy = AgentWorkspacePolicy(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = CursorAcpAdapter(policy)
        request = AgentRunRequest(
            task_id="TASK-CURSOR-IGNORE-APPLY",
            agent_session_id="AS-CURSOR-IGNORE-APPLY",
            resident="Cursor",
            provider="cursor",
            prompt="modify src/main.py",
            working_dir=project,
            model="cursor-grok-4.6-high",
        )
        ignore_parts = cursor_acp_module.CURSOR_WRITABLE_IGNORE_NAMES
        staging, baseline = adapter._prepare_staging_workspace(
            request.agent_session_id,
            request.working_dir,
            ignore_parts=ignore_parts,
        )
        try:
            (staging / "src" / "main.py").write_text("VALUE = 2\n", encoding="utf-8")

            async def emit(_event_type, _payload):
                return None

            async def approve(_request_id, _kind, _payload):
                return {"decision": "approve_once"}

            await adapter._review_and_apply_staged_changes(
                request,
                staging_dir=staging,
                review_dir=tmp_path / "cursor-review-ignore-apply",
                baseline=baseline,
                ignore_parts=ignore_parts,
                emit=emit,
                wait_for_master=approve,
            )

            assert (project / "src" / "main.py").read_text(encoding="utf-8") == "VALUE = 2\n"
            assert (project / "node_modules" / "dep" / "index.js").read_text(
                encoding="utf-8"
            ) == "dependency\n"
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_read_only_review_rejects_staging_mutation_and_stale_source(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    source = project / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\ProjectA"),
    )
    adapter = CursorAcpAdapter(policy)
    ignore_parts = adapter._read_only_staging_ignore_parts(project)

    staging, baseline = adapter._prepare_staging_workspace(
        "AS-READONLY-WRITE",
        project,
        ignore_parts=ignore_parts,
    )
    try:
        (staging / "module.py").write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="read-only review attempted to modify"):
            adapter._verify_read_only_review_unchanged(
                project,
                staging,
                baseline,
                ignore_parts=ignore_parts,
            )
        assert source.read_text(encoding="utf-8") == "VALUE = 1\n"
    finally:
        adapter._cleanup_staging_workspace(staging)

    staging, baseline = adapter._prepare_staging_workspace(
        "AS-READONLY-STALE",
        project,
        ignore_parts=ignore_parts,
    )
    try:
        source.write_text("VALUE = 3\n", encoding="utf-8")
        with pytest.raises(AgentRuntimeError, match="Review target changed"):
            adapter._verify_read_only_review_unchanged(
                project,
                staging,
                baseline,
                ignore_parts=ignore_parts,
            )
    finally:
        adapter._cleanup_staging_workspace(staging)


def test_cursor_native_conversation_load_reuses_session_without_replaying_old_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        (project / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        auth = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        auth.mkdir(parents=True)
        (auth / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        log_path = tmp_path / "cursor-native-log.jsonl"
        fake_server = tmp_path / "fake_cursor_native.py"
        fake_server.write_text(
            r'''import json
from pathlib import Path
import sys

LOG = Path(__LOG_PATH__)

def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()

def log(value):
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")

for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params", {})
    if method == "initialize":
        send({"jsonrpc":"2.0","id":request_id,"result":{
            "protocolVersion":1,
            "authMethods":[{"id":"cursor_login"}],
            "agentCapabilities":{"loadSession":True}
        }})
    elif method == "authenticate":
        send({"jsonrpc":"2.0","id":request_id,"result":{}})
    elif method == "session/new":
        log({"method":method,"cwd":params.get("cwd")})
        send({"jsonrpc":"2.0","id":request_id,"result":{
            "sessionId":"cursor-native-1","configOptions":[]
        }})
    elif method == "session/load":
        log({"method":method,"cwd":params.get("cwd"),"sessionId":params.get("sessionId")})
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"cursor-native-1",
            "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":"OLD-REPLAY-MUST-NOT-RETURN"}}
        }})
        send({"jsonrpc":"2.0","id":"replay-todo","method":"cursor/update_todos","params":{
            "toolCallId":"old-todo","todos":[{"id":"old","content":"OLD-REPLAY-TODO","status":"completed"}]
        }})
        replay_reply = json.loads(sys.stdin.readline())
        log({"method":"replay-todo-reply","result":replay_reply.get("result")})
        send({"jsonrpc":"2.0","method":"cursor/task","params":{
            "toolCallId":"old-task","description":"OLD-REPLAY-TASK","prompt":"historical","subagentType":"explore","agentId":"old-agent","durationMs":1
        }})
        send({"jsonrpc":"2.0","id":request_id,"result":{"configOptions":[]}})
    elif method == "session/prompt":
        prompt = params.get("prompt", [{}])[0].get("text", "")
        log({"method":method,"sessionId":params.get("sessionId"),"prompt":prompt})
        answer = "NEW-SECOND" if "second turn" in prompt else "NEW-FIRST"
        send({"jsonrpc":"2.0","method":"session/update","params":{
            "sessionId":"cursor-native-1",
            "update":{"sessionUpdate":"agent_message_chunk","content":{"type":"text","text":answer}}
        }})
        send({"jsonrpc":"2.0","id":request_id,"result":{"stopReason":"end_turn"}})
    elif request_id is not None:
        send({"jsonrpc":"2.0","id":request_id,"result":{}})
'''.replace("__LOG_PATH__", json.dumps(str(log_path))),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: (sys.executable, str(fake_server)),
        )
        policy = AgentWorkspacePolicy(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
        )
        adapter = CursorAcpAdapter(policy)

        async def no_master(*_args):
            raise AssertionError("read-only native Conversation must not ask Master")

        async def run_turn(agent_session_id: str, prompt: str, provider_session_id: str | None):
            events: list[tuple[str, dict[str, Any]]] = []

            async def emit(event_type, payload):
                events.append((event_type, payload))

            summary = await adapter.run(
                AgentRunRequest(
                    task_id=f"HC-{agent_session_id}",
                    agent_session_id=agent_session_id,
                    resident="Holo",
                    provider="cursor",
                    prompt=prompt,
                    working_dir=project.resolve(),
                    read_only=True,
                    purpose="consult",
                    conversation_id="CV-CURSOR-NATIVE",
                    provider_session_id=provider_session_id,
                ),
                emit=emit,
                wait_for_master=no_master,
            )
            native = next(
                payload.get("provider_session_id")
                for event_type, payload in events
                if event_type == "run_state" and payload.get("provider_session_id")
            )
            return summary, native, events

        first_summary, native, _first_events = await asyncio.wait_for(
            run_turn("AS-CURSOR-NATIVE-1", "first turn", None),
            timeout=10,
        )
        second_summary, resumed_native, second_events = await asyncio.wait_for(
            run_turn("AS-CURSOR-NATIVE-2", "second turn", str(native)),
            timeout=10,
        )

        assert first_summary == "NEW-FIRST"
        assert second_summary == "NEW-SECOND"
        assert "OLD-REPLAY-MUST-NOT-RETURN" not in second_summary
        assert not any(
            event_type in {"todo_update", "subagent_update"}
            and "OLD-REPLAY" in json.dumps(payload, ensure_ascii=False)
            for event_type, payload in second_events
        )
        assert native == resumed_native == "cursor-native-1"
        calls = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
        starts = [item for item in calls if item["method"] in {"session/new", "session/load"}]
        assert [item["method"] for item in starts] == ["session/new", "session/load"]
        assert starts[0]["cwd"] == starts[1]["cwd"]
        assert starts[1]["sessionId"] == "cursor-native-1"
        prompts = [item["prompt"] for item in calls if item["method"] == "session/prompt"]
        assert prompts == [
            CursorAcpAdapter._build_agent_prompt(AgentRunRequest(
                task_id="HC-AS-CURSOR-NATIVE-1",
                agent_session_id="AS-CURSOR-NATIVE-1",
                resident="Holo",
                provider="cursor",
                prompt="first turn",
                working_dir=Path(starts[0]["cwd"]),
                read_only=True,
                purpose="consult",
                conversation_id="CV-CURSOR-NATIVE",
            )),
            CursorAcpAdapter._build_agent_prompt(AgentRunRequest(
                task_id="HC-AS-CURSOR-NATIVE-2",
                agent_session_id="AS-CURSOR-NATIVE-2",
                resident="Holo",
                provider="cursor",
                prompt="second turn",
                working_dir=Path(starts[1]["cwd"]),
                read_only=True,
                purpose="consult",
                conversation_id="CV-CURSOR-NATIVE",
                provider_session_id="cursor-native-1",
            )),
        ]
        assert (project / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"

    asyncio.run(scenario())


def test_cursor_read_only_review_prompt_requires_structured_verdict_and_forbids_mutation(tmp_path: Path) -> None:
    request = AgentRunRequest(
        task_id="HR-CURSOR-REVIEW",
        agent_session_id="AS-CURSOR-REVIEW",
        resident="Holo",
        provider="cursor",
        prompt="Review the current Holo supervisor changes",
        working_dir=tmp_path,
        read_only=True,
    )

    prompt = CursorAcpAdapter._build_agent_prompt(request)

    assert "read-only Cursor reviewer" in prompt
    assert "Do not create, modify, move, or delete any file" in prompt
    assert "Do not run shell or terminal commands" in prompt
    assert "exactly SAFE or NEEDS FIX" in prompt
    assert "Do not fix them" in prompt


def test_cursor_staging_auto_applies_ordinary_changes_without_master_approval(tmp_path: Path) -> None:
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

            async def should_not_wait(*_args):
                raise AssertionError("ordinary staged changes must not stop for Master approval")

            await adapter._review_and_apply_staged_changes(
                request,
                staging_dir=staging,
                review_dir=tmp_path / "cursor-review-approved",
                baseline=baseline,
                emit=emit,
                wait_for_master=should_not_wait,
            )
            assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "staged\n"
            assert [kind for kind, _ in emitted] == ["file_change", "status_message", "file_change"]
            operation_id = emitted[0][1]["operation_id"]
            assert emitted[1][1]["operation_id"] == operation_id
            assert emitted[1][1]["kind"] == "cursor_stage_auto_apply"
            assert emitted[2][1]["status"] == "completed"
            assert emitted[0][1]["status"] == "pending_apply"
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

            async def emit(event_type, payload):
                if event_type == "status_message" and payload.get("kind") == "cursor_stage_auto_apply":
                    project.rmdir()

            async def should_not_wait(*_args):
                raise AssertionError("ordinary staged changes must not ask Master")

            with pytest.raises(AgentRuntimeError, match="was rolled back"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-deleted-root",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=should_not_wait,
                )
            assert project.exists() is False
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_staging_refuses_apply_if_staging_changes_during_major_destructive_review(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("delete most files\n", encoding="utf-8")
        for index in range(10):
            (request.working_dir / f"delete-{index}.txt").write_text("x\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            for index in range(10):
                (staging / f"delete-{index}.txt").unlink()

            async def emit(event_type, payload):
                return None

            async def approve_after_mutation(request_id, kind, payload):
                assert kind == "approval"
                (staging / "tampered.txt").write_text("tampered-after-review\n", encoding="utf-8")
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
            assert all((request.working_dir / f"delete-{index}.txt").is_file() for index in range(10))
        finally:
            adapter._cleanup_staging_workspace(staging)

    asyncio.run(scenario())


def test_cursor_major_destructive_reject_leaves_real_workspace_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("delete most files\n", encoding="utf-8")
        for index in range(10):
            (request.working_dir / f"delete-{index}.txt").write_text("x\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            for index in range(10):
                (staging / f"delete-{index}.txt").unlink()

            async def emit(event_type, payload):
                return None

            async def reject(request_id, kind, payload):
                assert kind == "approval"
                return {"decision": "reject"}

            with pytest.raises(AgentRuntimeError, match="rejected large destructive Cursor staged changes"):
                await adapter._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging,
                    review_dir=tmp_path / "cursor-review-rejected",
                    baseline=baseline,
                    emit=emit,
                    wait_for_master=reject,
                )
            assert all(
                (request.working_dir / f"delete-{index}.txt").read_text(encoding="utf-8") == "x\n"
                for index in range(10)
            )
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


def test_cursor_cancel_during_approved_apply_completes_written_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        request = _request(tmp_path)
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        staging, baseline = adapter._prepare_staging_workspace(request.agent_session_id, request.working_dir)
        try:
            (staging / "result.txt").write_text("applied-then-cancelled\n", encoding="utf-8")
            original_apply = adapter._apply_staged_changes
            entered = threading.Event()

            def slow_apply(*args, **kwargs):
                entered.set()
                time.sleep(0.15)
                return original_apply(*args, **kwargs)

            monkeypatch.setattr(adapter, "_apply_staged_changes", slow_apply)

            async def emit(event_type, payload):
                return None

            async def approve(request_id, kind, payload):
                return {"decision": "approve_once"}

            apply_task = asyncio.create_task(adapter._review_and_apply_staged_changes(
                request,
                staging_dir=staging,
                review_dir=tmp_path / "cursor-review-apply-cancel",
                baseline=baseline,
                emit=emit,
                wait_for_master=approve,
            ))
            for _ in range(80):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            apply_task.cancel()
            with pytest.raises(StagedApplyCancelledAfterCommit):
                await apply_task
            assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "applied-then-cancelled\n"
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


def test_cursor_apply_persists_recovery_manifest_before_first_real_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter = CursorAcpAdapter(_policy(tmp_path))
    request = _request(tmp_path)
    (request.working_dir / "task.md").write_text("modify a.txt\n", encoding="utf-8")
    (request.working_dir / "a.txt").write_text("before\n", encoding="utf-8")
    staging, baseline = adapter._prepare_staging_workspace(
        request.agent_session_id,
        request.working_dir,
    )
    try:
        (staging / "a.txt").write_text("after\n", encoding="utf-8")
        changes = adapter._collect_staged_changes(request.working_dir, staging, baseline)

        def stop_before_first_real_write(_source: Path, _target: Path) -> None:
            rollback_dirs = list(
                (tmp_path / "runtime" / "cursor_recovery").glob(".RB-*")
            )
            assert len(rollback_dirs) == 1
            rollback_root = rollback_dirs[0]
            manifest = json.loads(
                (rollback_root / "recovery.json").read_text(encoding="utf-8")
            )
            assert manifest["state"] == "applying"
            assert manifest["working_dir"] == str(request.working_dir)
            assert manifest["changes"] == [{
                "relative_path": "a.txt",
                "change_type": "modify",
                "backup_relative_path": "a.txt",
            }]
            assert (rollback_root / "a.txt").read_text(encoding="utf-8") == "before\n"
            raise KeyboardInterrupt("simulate hard stop before real write")

        monkeypatch.setattr(adapter, "_atomic_copy_file", stop_before_first_real_write)
        with pytest.raises(KeyboardInterrupt, match="simulate hard stop"):
            adapter._apply_staged_changes(
                request.working_dir,
                staging,
                baseline,
                changes,
            )
        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "before\n"
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
        assert waited == []
        assert [kind for kind, _ in emitted[:2]] == ["file_change", "status_message"]
        assert emitted[0][1]["operation_id"] == "edit-1"
        assert emitted[0][1]["changes"][0]["relative_path"] == "result.txt"
        assert emitted[1][1]["kind"] == "cursor_tool_auto_allowed"

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
        browser = await adapter._handle_permission_request(
            {
                "toolCall": {"toolCallId": "browser-1", "kind": "browser", "title": "Open browser"},
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        computer = await adapter._handle_permission_request(
            {
                "toolCall": {"toolCallId": "computer-1", "kind": "computer_use", "title": "Use computer"},
                "options": [{"optionId": "allow-once"}, {"optionId": "reject-once"}],
            },
            request=request,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        assert external == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert browser == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert computer == {"outcome": {"outcome": "selected", "optionId": "reject-once"}}
        assert waited == []
        assert sum(
            kind == "status_message" and payload.get("kind") == "external_tool_blocked"
            for kind, payload in emitted
        ) >= 3

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
        assert [kind for kind, _ in emitted] == ["question_request", "plan", "status_message"]
        assert emitted[-1][1]["kind"] == "cursor_plan_auto_accepted"

    asyncio.run(scenario())


def test_cursor_notification_extensions_accept_live_request_shape(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = CursorAcpAdapter(_policy(tmp_path))
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        todos = [
            {"id": "1", "content": "Inspect", "status": "completed"},
            {"id": "2", "content": "Review", "status": "in_progress"},
        ]
        todo_result = await adapter._handle_notification_extension_request(
            "cursor/update_todos",
            {"toolCallId": "todo-live", "todos": todos, "merge": True},
            emit=emit,
        )
        task_result = await adapter._handle_notification_extension_request(
            "cursor/task",
            {
                "toolCallId": "task-live",
                "description": "Explore codebase",
                "prompt": "Find the relevant files",
                "subagentType": "explore",
                "agentId": "agent-live",
                "durationMs": 321,
            },
            emit=emit,
        )
        image_result = await adapter._handle_notification_extension_request(
            "cursor/generate_image",
            {"toolCallId": "image-live", "description": "Generate an icon"},
            emit=emit,
        )
        unknown_result = await adapter._handle_notification_extension_request(
            "cursor/unknown_extension",
            {},
            emit=emit,
        )

        assert todo_result == {"outcome": {"outcome": "accepted", "todos": todos}}
        assert task_result == {
            "outcome": {"outcome": "completed", "agentId": "agent-live", "durationMs": 321}
        }
        assert image_result == {
            "outcome": {
                "outcome": "rejected",
                "reason": "Nirai does not expose Cursor image generation through Agent Runtime",
            }
        }
        assert unknown_result is None
        assert emitted == [
            (
                "todo_update",
                {"operation_id": "todo-live", "steps": todos, "merge": True},
            ),
            (
                "subagent_update",
                {
                    "operation_id": "task-live",
                    "subagent_type": "explore",
                    "description": "Explore codebase",
                    "prompt": "Find the relevant files",
                    "agent_id": "agent-live",
                    "duration_ms": 321,
                },
            ),
        ]

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


def test_cursor_successful_apply_is_not_failed_by_final_staging_cleanup_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "model": "cursor-grok-4.6-xhigh"})

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                (Path(cwd) / "result.txt").write_text("applied\n", encoding="utf-8")
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "implemented",
                        "session_id": "cursor-cleanup-success-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]
        original_cleanup = adapter._cleanup_staging_workspace

        def fail_only_final_stage_cleanup(path: Path) -> None:
            if (
                path.name == ".cursor-stage-AS-CURSOR"
                and (request.working_dir / "result.txt").exists()
            ):
                raise AgentRuntimeError("simulated final staging cleanup failure")
            original_cleanup(path)

        monkeypatch.setattr(adapter, "_cleanup_staging_workspace", fail_only_final_stage_cleanup)
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def approve(_request_id, _kind, _payload):
            return {"decision": "approve_once"}

        summary = await adapter.run(request, emit=emit, wait_for_master=approve)

        assert summary == "implemented"
        assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "applied\n"
        assert any(
            event_type == "error"
            and payload.get("code") == "provider_cleanup_failed"
            and "staging cleanup failure" in payload.get("message", "")
            for event_type, payload in emitted
        )

    asyncio.run(scenario())


def test_cursor_exact_xhigh_cancel_after_staged_commit_returns_committed_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "model": "cursor-grok-4.6-xhigh"})

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "implemented before cancel",
                        "session_id": "cursor-committed-cancel-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]

        async def committed_then_cancelled(*_args, **_kwargs):
            raise StagedApplyCancelledAfterCommit

        monkeypatch.setattr(adapter, "_complete_staged_work", committed_then_cancelled)

        async def emit(_event_type, _payload):
            return None

        async def no_master(*_args):
            raise AssertionError("synthetic committed-cancel path must not ask Master")

        result = await adapter.run(request, emit=emit, wait_for_master=no_master)
        assert isinstance(result, AgentRunResult)
        assert result.summary == "implemented before cancel"
        assert result.work_committed is True

    asyncio.run(scenario())


def test_cursor_exact_xhigh_work_uses_cli_staging_and_master_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = _request(tmp_path)
        request = AgentRunRequest(
            **{
                **request.__dict__,
                "model": "cursor-grok-4.6-xhigh",
            }
        )
        (request.working_dir / "task.md").write_text("create result.txt\n", encoding="utf-8")
        calls: list[dict[str, Any]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                assert cwd != request.working_dir
                assert not (request.working_dir / "result.txt").exists()
                (cwd / "result.txt").write_text("xhigh staged\n", encoding="utf-8")
                config = json.loads((Path(env["CURSOR_CONFIG_DIR"]) / "cli-config.json").read_text(encoding="utf-8"))
                calls.append({
                    "invocation_id": invocation_id,
                    "argv": tuple(argv),
                    "cwd": cwd,
                    "timeout_sec": timeout_sec,
                    "stdin_text": stdin_text,
                    "config": config,
                })
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "implemented with xhigh",
                        "session_id": "cursor-xhigh-work-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def approve(_request_id, kind, payload):
            assert kind == "approval"
            assert payload["kind"] == "file_change"
            assert not (request.working_dir / "result.txt").exists()
            return {"decision": "approve_once"}

        summary = await adapter.run(request, emit=emit, wait_for_master=approve)

        assert summary == "implemented with xhigh"
        assert (request.working_dir / "result.txt").read_text(encoding="utf-8") == "xhigh staged\n"
        assert len(calls) == 1
        argv = calls[0]["argv"]
        assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"
        assert "--force" in argv
        assert "--mode" not in argv
        assert not any("fast" in item.casefold() for item in argv)
        deny = calls[0]["config"]["permissions"]["deny"]
        assert "Shell(*)" in deny
        assert "WebFetch(*)" in deny
        assert "WebSearch(*)" in deny
        assert "Mcp(*:*)" in deny
        assert any(
            event_type == "run_state" and payload.get("provider_session_id") == "cursor-xhigh-work-1"
            for event_type, payload in emitted
        )

    asyncio.run(scenario())


def test_cursor_exact_xhigh_cancel_intent_treats_nonzero_exit_as_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        request = _request(tmp_path)
        request = AgentRunRequest(**{**request.__dict__, "model": "cursor-grok-4.6-xhigh"})
        emitted: list[tuple[str, dict[str, Any]]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                assert any(
                    event_type == "run_state" and payload.get("state") == "running"
                    for event_type, payload in emitted
                )
                adapter.mark_cancel_intent(invocation_id)
                return CompletedInvocation(1, "", "cancelled by test")

            async def cancel(self, _invocation_id: str) -> bool:
                return True

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]

        async def emit(event_type, payload):
            emitted.append((event_type, payload))

        async def no_master(*_args):
            raise AssertionError("cancelled CLI must not reach Master approval")

        with pytest.raises(asyncio.CancelledError):
            await adapter.run(request, emit=emit, wait_for_master=no_master)

        assert any(
            event_type == "status_message"
            and payload.get("kind") == "provider_process_started"
            for event_type, payload in emitted
        )

    asyncio.run(scenario())


def test_cursor_exact_xhigh_review_uses_read_only_cli_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        profile = tmp_path / "runtime" / "cursor_profile" / ".cursor"
        profile.mkdir(parents=True)
        (profile / "agent-cli-state.json").write_text("{}\n", encoding="utf-8")
        adapter = CursorAcpAdapter(_policy(tmp_path))
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda _path: None)
        monkeypatch.setattr(
            cursor_acp_module,
            "resolve_cursor_command",
            lambda: ("node.exe", "cursor-index.js"),
        )
        working = tmp_path / "runtime" / "workspace" / "HR-XHIGH"
        working.mkdir(parents=True)
        (working / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        request = AgentRunRequest(
            task_id="HR-XHIGH",
            agent_session_id="AS-XHIGH-REVIEW",
            resident="Holo",
            provider="cursor",
            prompt="Review module.py",
            working_dir=working,
            model="cursor-grok-4.6-xhigh",
            read_only=True,
            purpose="review",
            conversation_id="CV-XHIGH-REVIEW",
            provider_session_id="cursor-xhigh-review-1",
        )
        calls: list[dict[str, Any]] = []

        class FakeCliProcessManager:
            async def run(self, invocation_id, argv, *, cwd, timeout_sec, stdin_text=None, env=None):
                calls.append({
                    "invocation_id": invocation_id,
                    "argv": tuple(argv),
                    "cwd": cwd,
                    "stdin_text": stdin_text,
                })
                return CompletedInvocation(
                    0,
                    json.dumps({
                        "type": "result",
                        "subtype": "success",
                        "is_error": False,
                        "result": "Checked module.py first.SAFE\nNo blocking finding.",
                        "session_id": "cursor-xhigh-review-1",
                    }),
                    "",
                )

            async def cancel(self, _invocation_id: str) -> bool:
                return False

        adapter._cli_process_manager = FakeCliProcessManager()  # type: ignore[assignment]

        async def emit(_event_type, _payload):
            return None

        async def no_master(*_args):
            raise AssertionError("read-only xhigh review must not ask Master")

        summary = await adapter.run(request, emit=emit, wait_for_master=no_master)

        assert summary.startswith("SAFE")
        assert (working / "module.py").read_text(encoding="utf-8") == "VALUE = 1\n"
        argv = calls[0]["argv"]
        assert argv[argv.index("--model") + 1] == "cursor-grok-4.6-xhigh"
        assert argv[argv.index("--resume") + 1] == "cursor-xhigh-review-1"
        assert argv[argv.index("--mode") + 1] == "ask"
        assert "--force" not in argv
        assert not any("fast" in item.casefold() for item in argv)
        assert "exactly SAFE or NEEDS FIX" in calls[0]["stdin_text"]

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
