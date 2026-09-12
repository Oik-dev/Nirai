from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import threading
import time
import sys
from types import SimpleNamespace

import pytest

from core.agents import (
    AgentProviderLimitError,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeError,
    AgentRuntimeManager,
    AgentRuntimeUnavailableError,
    AgentWorkspacePolicy,
    CodexAppServerAdapter,
)
from core.agents.codex_app_server import (
    _JsonLineAppServer,
    _codex_approval_requires_master,
    _common_approval_payload,
    _terminate_process_tree,
    _validate_file_change_approval,
)
from core.agents.codex_events import normalize_codex_item, normalize_codex_notification
from core.agents.safety import AgentSafetyError


def test_codex_normalizer_maps_p0_items_without_exposing_reasoning(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    working_dir = policy.resolve_working_dir(None, task_id="TASK-NORMALIZE")

    command = normalize_codex_item(
        {
            "id": "cmd-1",
            "type": "commandExecution",
            "command": "python -m pytest",
            "cwd": str(working_dir),
            "status": "completed",
            "commandActions": [],
            "aggregatedOutput": "3 passed",
            "exitCode": 0,
            "durationMs": 123,
        },
        phase="completed",
        working_dir=working_dir,
        workspace_policy=policy,
    )
    file_change = normalize_codex_item(
        {
            "id": "file-1",
            "type": "fileChange",
            "status": "completed",
            "changes": [{
                "path": str(working_dir / "result.txt"),
                "diff": "+done",
                "kind": {"type": "add"},
            }],
        },
        phase="completed",
        working_dir=working_dir,
        workspace_policy=policy,
    )
    reasoning = normalize_codex_item(
        {"id": "reason-1", "type": "reasoning", "content": ["private chain"]},
        phase="completed",
        working_dir=working_dir,
        workspace_policy=policy,
    )

    assert command[0][0] == "command_execution"
    assert command[0][1]["output"] == "3 passed"
    assert file_change[0][0] == "file_change"
    assert file_change[0][1]["changes"][0]["relative_path"] == "result.txt"
    assert reasoning == []


def test_codex_normalizer_maps_diff_plan_todo_and_drops_streaming_output_delta(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    working_dir = policy.resolve_working_dir(None, task_id="TASK-NOTIFY")

    plan_events = normalize_codex_notification(
        "turn/plan/updated",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "explanation": "進める",
            "plan": [
                {"step": "調査", "status": "completed"},
                {"step": "実装", "status": "inProgress"},
            ],
        },
        working_dir=working_dir,
        workspace_policy=policy,
    )
    diff_events = normalize_codex_notification(
        "turn/diff/updated",
        {"threadId": "thread-1", "turnId": "turn-1", "diff": "+hello"},
        working_dir=working_dir,
        workspace_policy=policy,
    )
    delta_events = normalize_codex_notification(
        "item/commandExecution/outputDelta",
        {"threadId": "thread-1", "turnId": "turn-1", "itemId": "cmd-1", "delta": "ok\n"},
        working_dir=working_dir,
        workspace_policy=policy,
    )

    assert [event[0] for event in plan_events] == ["plan", "todo_update"]
    assert plan_events[1][1]["steps"][1]["status"] == "inProgress"
    assert diff_events == [("diff", {"diff": "+hello"})]
    assert delta_events == []


def test_codex_staged_command_gate_is_reserved_for_commit_and_push() -> None:
    file_method = "item/fileChange/requestApproval"
    command_method = "item/commandExecution/requestApproval"

    assert _codex_approval_requires_master(file_method, {"itemId": "file-1"}) is False
    assert _codex_approval_requires_master(command_method, {"command": "python -m pytest"}) is False
    assert _codex_approval_requires_master(command_method, {"command": ["npm.cmd", "run", "build"]}) is False
    assert _codex_approval_requires_master(command_method, {"command": "git commit -am test"}) is True
    assert _codex_approval_requires_master(command_method, {"command": "git push origin main"}) is True
    assert _codex_approval_requires_master(command_method, {"command": "git -C project push origin main"}) is True
    assert _codex_approval_requires_master(command_method, {"command": "git status && echo commit"}) is False
    # All local filesystem effects occur only in staging and are judged from the
    # frozen aggregate diff after provider shutdown, regardless of spelling.
    assert _codex_approval_requires_master(command_method, {"command": "git reset --hard HEAD~1"}) is False
    assert _codex_approval_requires_master(command_method, {"command": "rm -rf generated"}) is False
    assert _codex_approval_requires_master(command_method, {"command": "ri generated -Recurse -Force"}) is False
    assert _codex_approval_requires_master(command_method, {
        "command": "python -c \"import shutil; shutil.rmtree('generated')\""
    }) is False
    assert _codex_approval_requires_master(command_method, {}) is True


def test_file_change_approval_keeps_item_id_and_validates_grant_root_before_master(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    working_dir = policy.resolve_working_dir(None, task_id="TASK-APPROVAL")
    allowed_root = working_dir / "subdir"
    allowed_root.mkdir()

    item_id, grant_root = _validate_file_change_approval(
        {"itemId": "file-42", "grantRoot": str(allowed_root)},
        workspace_policy=policy,
        working_dir=working_dir,
    )
    payload = _common_approval_payload(
        "provider-request-1",
        "item/fileChange/requestApproval",
        {"itemId": item_id, "grantRoot": grant_root, "reason": "write files"},
    )
    assert payload["operation_id"] == "file-42"
    assert payload["grant_root"] == str(allowed_root)

    try:
        _validate_file_change_approval(
            {"itemId": "file-escape", "grantRoot": str(tmp_path / "outside")},
            workspace_policy=policy,
            working_dir=working_dir,
        )
    except AgentSafetyError:
        pass
    else:
        raise AssertionError("outside grantRoot was accepted")

    try:
        _validate_file_change_approval(
            {"grantRoot": str(working_dir)},
            workspace_policy=policy,
            working_dir=working_dir,
        )
    except AgentSafetyError:
        pass
    else:
        raise AssertionError("File Change approval without itemId was accepted")


def test_codex_app_server_usage_fetch_reads_rate_limits_and_cleans_isolated_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        fake_server = tmp_path / "fake_codex_usage_server.py"
        fake_server.write_text(_FAKE_CODEX_USAGE_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]

        payload = await asyncio.wait_for(adapter.fetch_rate_limits(), timeout=5.0)

        assert payload["rateLimits"]["primary"]["windowDurationMins"] == 300
        assert payload["rateLimits"]["secondary"]["windowDurationMins"] == 10080
        assert adapter._runtime_owned_snapshot() == set()
        homes = tmp_path / "runtime" / "codex_agent_homes"
        assert not homes.exists() or list(homes.iterdir()) == []

    asyncio.run(scenario())


@pytest.mark.parametrize("error_channel", ["turn", "rpc"])
def test_codex_quota_failures_preserve_partial_work_and_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_channel: str,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="T-QUOTA")
        fake_server = tmp_path / "quota_server.py"
        fake_server.write_text('''
import json
import sys
from pathlib import Path

def send(value):
    print(json.dumps(value), flush=True)

for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize":
        send({"id": request_id, "result": {}})
    elif method == "thread/start":
        working = Path(message["params"]["cwd"])
        send({"id": request_id, "result": {"thread": {"id": "quota-thread"}}})
    elif method == "turn/start":
        (working / "partial.txt").write_text("recoverable edit", encoding="utf-8")
        error = {"code": "quota_exhausted", "message": "Provider capacity is unavailable"}
        if sys.argv[1] == "rpc":
            send({"id": request_id, "error": error})
        else:
            send({"id": request_id, "result": {"turn": {"id": "quota-turn"}}})
            send({"method": "turn/completed", "params": {"turn": {
                "id": "quota-turn", "status": "failed", "error": error,
            }}})
''', encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server), error_channel)  # type: ignore[method-assign]

        async def emit(*_args):
            pass

        with pytest.raises(AgentProviderLimitError) as caught:
            await asyncio.wait_for(adapter.run(AgentRunRequest(
                task_id="T-QUOTA", agent_session_id="AS-QUOTA", resident="Codex",
                provider="codex", prompt="edit file", working_dir=working,
            ), emit=emit, wait_for_master=emit), 5)
        assert caught.value.partial_work_path is not None
        assert (Path(caught.value.partial_work_path) / "files" / "partial.txt").read_text() == "recoverable edit"
        assert not (working / "partial.txt").exists()
        assert adapter._runtime_owned_snapshot() == set()
        homes = tmp_path / "runtime" / "codex_agent_homes"
        assert not homes.exists() or list(homes.iterdir()) == []

    asyncio.run(scenario())


def test_codex_app_server_adapter_runs_turn_and_bridges_approval_and_question(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    (source_home / "AGENTS.md").write_text("must not leak\n", encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "provider-default-model"\nmodel_reasoning_effort = "high"\n',
        encoding="utf-8",
    )
    skill_dir = source_home / "skills" / "global-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("must not leak\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working_dir = policy.resolve_working_dir(None, task_id="TASK-CODEX")
        fake_server = tmp_path / "fake_codex_app_server.py"
        fake_server.write_text(_FAKE_CODEX_SERVER, encoding="utf-8")

        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        events: list[tuple[str, dict[str, object]]] = []
        master_requests: list[tuple[str, str, dict[str, object]]] = []

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def wait_for_master(
            request_id: str,
            kind: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            master_requests.append((request_id, kind, payload))
            if kind == "approval":
                return {"decision": "approve_once"}
            return {"answers": {"q1": ["テストを続けて"]}}

        summary = await asyncio.wait_for(
            adapter.run(
                AgentRunRequest(
                    task_id="TASK-CODEX",
                    agent_session_id="AGENT-CODEX",
                    resident="Codex",
                    provider="codex",
                    prompt="テスト作業をして",
                    working_dir=working_dir,
                ),
                emit=emit,  # type: ignore[arg-type]
                wait_for_master=wait_for_master,  # type: ignore[arg-type]
            ),
            timeout=5.0,
        )

        assert summary == "作業完了"
        assert [request[1] for request in master_requests] == ["approval", "question"]
        assert master_requests[1][2]["questions"][0]["is_secret"] is True
        assert any(event_type == "command_execution" for event_type, _ in events)
        assert any(event_type == "file_change" for event_type, _ in events)
        assert any(event_type == "diff" for event_type, _ in events)
        assert any(event_type == "plan" for event_type, _ in events)
        assert any(event_type == "todo_update" for event_type, _ in events)
        assert any(
            event_type == "assistant_message" and payload.get("text") == "作業完了"
            for event_type, payload in events
        )
        assert not any("private chain" in json.dumps(payload, ensure_ascii=False) for _, payload in events)
        serialized_events = json.dumps(events, ensure_ascii=False)
        assert "provider_method" not in serialized_events
        assert '"thread_id"' not in serialized_events
        assert '"turn_id"' not in serialized_events
        # provider_session_id is an internal run_state handoff to
        # AgentRuntimeManager. The Manager removes it from the persisted/event
        # payload while storing it on the Session snapshot; it must not leak on
        # ordinary provider events.
        assert all(
            "provider_session_id" not in payload
            for event_type, payload in events
            if event_type != "run_state"
        )
        assert '"provider_turn_id"' not in serialized_events
        assert '"details"' not in serialized_events
        assert not (tmp_path / "runtime" / "codex_agent_homes" / "AGENT-CODEX").exists()

    asyncio.run(scenario())


def test_codex_writable_turn_applies_safe_changes_from_isolated_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working_dir = policy.resolve_working_dir(None, task_id="TASK-CODEX-STAGE-SAFE")
        (working_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
        fake_server = tmp_path / "fake_codex_staged_mutation.py"
        fake_server.write_text(_FAKE_CODEX_STAGED_MUTATION_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_spawn = adapter._spawn
        spawn_context: dict[str, object] = {}

        async def checked_spawn(command, provider_working_dir, *, env=None):
            spawn_context["cwd"] = provider_working_dir
            spawn_context["git_ceiling"] = None if env is None else env.get("GIT_CEILING_DIRECTORIES")
            return await real_spawn(command, provider_working_dir, env=env)

        monkeypatch.setattr(adapter, "_spawn", checked_spawn)
        events: list[tuple[str, dict[str, object]]] = []

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def should_not_wait(*_args, **_kwargs):
            raise AssertionError("safe staged Codex work must not ask Master")

        summary = await adapter.run(
            AgentRunRequest(
                task_id="TASK-CODEX-STAGE-SAFE",
                agent_session_id="AS-CODEX-STAGE-SAFE",
                resident="Codex",
                provider="codex",
                prompt="safe",
                working_dir=working_dir,
            ),
            emit=emit,
            wait_for_master=should_not_wait,
        )

        assert summary == "staged mutation complete"
        assert spawn_context["cwd"] != working_dir
        assert spawn_context["git_ceiling"] == str(spawn_context["cwd"])
        assert (working_dir / "safe.txt").read_text(encoding="utf-8") == "safe from stage\n"
        assert (working_dir / "keep.txt").read_text(encoding="utf-8") == "keep\n"
        assert any(
            kind == "status_message" and payload.get("kind") == "codex_stage_auto_apply"
            for kind, payload in events
        )
        assert not (policy.default_workspace_root / ".cursor-stage-AS-CODEX-STAGE-SAFE").exists()

    asyncio.run(scenario())


def test_codex_arbitrary_script_mass_delete_is_staged_and_rejected_before_real_workspace_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working_dir = policy.resolve_working_dir(None, task_id="TASK-CODEX-STAGE-DELETE")
        for index in range(30):
            (working_dir / f"victim-{index:02d}.txt").write_text(f"victim {index}\n", encoding="utf-8")
        fake_server = tmp_path / "fake_codex_staged_delete.py"
        fake_server.write_text(_FAKE_CODEX_STAGED_MUTATION_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        master_requests: list[tuple[str, str, dict[str, object]]] = []
        events: list[tuple[str, dict[str, object]]] = []

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def wait_for_master(
            request_id: str,
            kind: str,
            payload: dict[str, object],
        ) -> dict[str, object]:
            master_requests.append((request_id, kind, payload))
            return {"decision": "reject"}

        with pytest.raises(AgentRuntimeError, match="rejected large destructive Codex staged changes"):
            await adapter.run(
                AgentRunRequest(
                    task_id="TASK-CODEX-STAGE-DELETE",
                    agent_session_id="AS-CODEX-STAGE-DELETE",
                    resident="Codex",
                    provider="codex",
                    prompt="delete-many",
                    working_dir=working_dir,
                ),
                emit=emit,
                wait_for_master=wait_for_master,
            )

        # The arbitrary Python deletion command itself was auto-approved because
        # it could affect only staging. The one Master request is the aggregate
        # 30-file frozen diff, and rejecting it leaves every real file intact.
        assert len(master_requests) == 1
        request_id, kind, payload = master_requests[0]
        assert request_id.startswith("codex-stage-apply-")
        assert kind == "approval"
        assert payload["kind"] == "file_change"
        assert payload["title"] == "Codex staged changes contain large destructive deletion"
        assert len(list(working_dir.glob("victim-*.txt"))) == 30
        assert any(
            event_type == "status_message"
            and event_payload.get("kind") == "codex_operation_auto_approved"
            for event_type, event_payload in events
        )
        assert not (policy.default_workspace_root / ".cursor-stage-AS-CODEX-STAGE-DELETE").exists()

    asyncio.run(scenario())


def test_codex_safe_workspace_operations_auto_approve_without_master(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working_dir = policy.resolve_working_dir(None, task_id="TASK-CODEX-AUTO")
        fake_server = tmp_path / "fake_codex_auto_approval.py"
        fake_server.write_text(_FAKE_CODEX_AUTO_APPROVAL_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        events: list[tuple[str, dict[str, object]]] = []

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def should_not_wait(*_args, **_kwargs):
            raise AssertionError("safe Codex workspace operations must not ask Master")

        summary = await asyncio.wait_for(
            adapter.run(
                AgentRunRequest(
                    task_id="TASK-CODEX-AUTO",
                    agent_session_id="AS-CODEX-AUTO",
                    resident="Codex",
                    provider="codex",
                    prompt="run safe local work",
                    working_dir=working_dir,
                ),
                emit=emit,
                wait_for_master=should_not_wait,
            ),
            timeout=5.0,
        )
        assert summary == "auto approved"
        assert not any(kind == "approval_request" for kind, _ in events)
        assert sum(
            kind == "status_message" and payload.get("kind") == "codex_operation_auto_approved"
            for kind, payload in events
        ) == 2

    asyncio.run(scenario())


def test_codex_agent_startup_removes_legacy_workspace_credential_home(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))
    legacy_home = tmp_path / "runtime" / "workspace" / "m4-codex-agent-home"
    legacy_home.mkdir(parents=True)
    (legacy_home / "auth.json").write_text("stale-test-copy\n", encoding="utf-8")

    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    adapter = CodexAppServerAdapter(policy)
    isolated = adapter._prepare_isolated_codex_home("AGENT-CLEANUP")
    try:
        assert not legacy_home.exists()
        assert (isolated / "auth.json").is_file()
    finally:
        adapter._remove_isolated_home(isolated)


def test_codex_run_claims_home_before_prepare_and_releases_claim_on_prepare_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        adapter = CodexAppServerAdapter(policy)
        working = tmp_path / "runtime" / "workspace" / "TASK-PREP-FAIL"
        working.mkdir(parents=True)
        observed_owned: list[bool] = []

        monkeypatch.setattr(adapter, "_resolve_command", lambda: ("fake-codex",))

        def fail_prepare(agent_session_id: str, *, conversation_id: str | None = None) -> Path:
            observed_owned.append(agent_session_id in adapter._runtime_owned_snapshot())
            raise AgentRuntimeUnavailableError("simulated prepare failure")

        monkeypatch.setattr(adapter, "_prepare_isolated_codex_home", fail_prepare)

        async def emit(_event_type, _payload):
            return None

        async def wait_for_master(*_args, **_kwargs):
            return {"decision": "reject"}

        with pytest.raises(AgentRuntimeUnavailableError, match="simulated prepare failure"):
            await adapter.run(
                AgentRunRequest(
                    task_id="TASK-PREP-FAIL",
                    agent_session_id="AS-PREP-FAIL",
                    resident="Codex",
                    provider="codex",
                    prompt="test",
                    working_dir=working,
                ),
                emit=emit,
                wait_for_master=wait_for_master,
            )

        assert observed_owned == [True]
        assert adapter._runtime_owned_snapshot() == set()

    asyncio.run(scenario())


def test_codex_cancel_during_spawn_reaps_late_process_and_credential_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="TASK-SPAWN-CANCEL")
        fake_server = tmp_path / "fake_codex_spawn_cancel.py"
        fake_server.write_text(_FAKE_CODEX_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_spawn = adapter._spawn
        spawn_created = asyncio.Event()
        spawn_release = asyncio.Event()
        holder: dict[str, object] = {}

        async def delayed_spawn(command, working_dir, *, env=None):
            process = await real_spawn(command, working_dir, env=env)
            holder["process"] = process
            spawn_created.set()
            await spawn_release.wait()
            return process

        monkeypatch.setattr(adapter, "_spawn", delayed_spawn)

        async def emit(_event_type, _payload):
            return None

        async def wait_for_master(*_args, **_kwargs):
            return {"decision": "reject"}

        task = asyncio.create_task(adapter.run(
            AgentRunRequest(
                task_id="TASK-SPAWN-CANCEL",
                agent_session_id="AS-SPAWN-CANCEL",
                resident="Codex",
                provider="codex",
                prompt="cancel during spawn",
                working_dir=working,
            ),
            emit=emit,
            wait_for_master=wait_for_master,
        ))
        await asyncio.wait_for(spawn_created.wait(), timeout=1.0)
        task.cancel()
        spawn_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)

        process = holder["process"]
        home = tmp_path / "runtime" / "codex_agent_homes" / "AS-SPAWN-CANCEL"
        try:
            assert getattr(process, "returncode") is not None
            assert not home.exists()
            assert adapter._runtime_owned_snapshot() == set()
        finally:
            if getattr(process, "returncode") is None:
                await _terminate_process_tree(process)  # type: ignore[arg-type]
            if home.exists():
                adapter._remove_isolated_home(home)

    asyncio.run(scenario())


def test_codex_cancel_after_spawn_before_active_registration_reaps_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="TASK-ACTIVE-LOCK-CANCEL")
        fake_server = tmp_path / "fake_codex_active_lock_cancel.py"
        fake_server.write_text(_FAKE_CODEX_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_spawn = adapter._spawn
        spawned = asyncio.Event()
        holder: dict[str, object] = {}

        async def tracked_spawn(command, working_dir, *, env=None):
            process = await real_spawn(command, working_dir, env=env)
            holder["process"] = process
            spawned.set()
            return process

        monkeypatch.setattr(adapter, "_spawn", tracked_spawn)
        await adapter._active_lock.acquire()

        async def emit(_event_type, _payload):
            return None

        async def wait_for_master(*_args, **_kwargs):
            return {"decision": "reject"}

        task = asyncio.create_task(adapter.run(
            AgentRunRequest(
                task_id="TASK-ACTIVE-LOCK-CANCEL",
                agent_session_id="AS-ACTIVE-LOCK-CANCEL",
                resident="Codex",
                provider="codex",
                prompt="cancel before active registration",
                working_dir=working,
            ),
            emit=emit,
            wait_for_master=wait_for_master,
        ))
        await asyncio.wait_for(spawned.wait(), timeout=1.0)
        task.cancel()
        await asyncio.sleep(0)
        adapter._active_lock.release()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2.0)

        process = holder["process"]
        home = tmp_path / "runtime" / "codex_agent_homes" / "AS-ACTIVE-LOCK-CANCEL"
        try:
            assert getattr(process, "returncode") is not None
            assert not home.exists()
            assert adapter._runtime_owned_snapshot() == set()
        finally:
            if adapter._active_lock.locked():
                adapter._active_lock.release()
            if getattr(process, "returncode") is None:
                await _terminate_process_tree(process)  # type: ignore[arg-type]
            if home.exists():
                adapter._remove_isolated_home(home)

    asyncio.run(scenario())


def test_codex_stale_home_cleanup_preserves_owned_and_young_runtime_homes(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    adapter = CodexAppServerAdapter(policy)
    root = tmp_path / "runtime" / "codex_agent_homes"
    owned = root / "AS-OWNED"
    young = root / "AS-YOUNG"
    stale = root / "AS-STALE"
    for home in (owned, young, stale):
        home.mkdir(parents=True)
        (home / "auth.json").write_text("test-only\n", encoding="utf-8")
    old = time.time() - (7 * 60 * 60)
    os.utime(owned, (old, old))
    os.utime(stale, (old, old))

    adapter._claim_runtime_id("AS-OWNED")
    try:
        adapter._cleanup_stale_agent_homes()
        assert owned.exists()
        assert young.exists()
        assert not stale.exists()
    finally:
        adapter._release_runtime_id("AS-OWNED")
        adapter._remove_isolated_home(owned)
        adapter._remove_isolated_home(young)


def test_codex_agent_credential_cleanup_retries_transient_windows_file_lock_with_bounded_backoff(
    tmp_path: Path,
    monkeypatch,
) -> None:
    target = tmp_path / "credential-home"
    target.mkdir()
    (target / "auth.json").write_text("test-only\n", encoding="utf-8")

    import core.agents.codex_app_server as codex_app_server

    real_rmtree = codex_app_server.shutil.rmtree
    attempts: list[Path] = []
    sleeps: list[float] = []

    def flaky_rmtree(path: Path) -> None:
        attempts.append(Path(path))
        if len(attempts) < 4:
            raise PermissionError("simulated transient WinError 32 lock")
        real_rmtree(path)

    monkeypatch.setattr(codex_app_server.shutil, "rmtree", flaky_rmtree)
    monkeypatch.setattr(codex_app_server.time, "sleep", lambda seconds: sleeps.append(seconds))
    CodexAppServerAdapter._remove_isolated_home(target)

    assert attempts == [target, target, target, target]
    assert sleeps == [0.05, 0.1, 0.2]
    assert sum(sleeps) < 1.0
    assert not target.exists()


def test_codex_agent_child_env_drops_unrelated_secrets(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-leak")
    monkeypatch.setenv("NIRAI_TEST_SECRET", "must-not-leak")
    isolated = tmp_path / "isolated-home"
    env = CodexAppServerAdapter._build_child_env(isolated)

    assert env["CODEX_HOME"] == str(isolated)
    assert env["USERPROFILE"] == str(isolated)
    assert env["HOME"] == str(isolated)
    assert "GEMINI_API_KEY" not in env
    assert "NIRAI_TEST_SECRET" not in env


def test_codex_agent_home_cleanup_runs_off_event_loop(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        adapter = CodexAppServerAdapter(policy)
        isolated = tmp_path / "runtime" / "codex_agent_homes" / "AS-SLOW-CLEANUP"
        isolated.mkdir(parents=True)
        (isolated / "auth.json").write_text("test-only\n", encoding="utf-8")
        cleanup_started = threading.Event()

        class Client:
            async def close(self) -> None:
                return None

        def slow_remove(path: Path) -> None:
            cleanup_started.set()
            time.sleep(0.2)
            import shutil

            shutil.rmtree(path)

        monkeypatch.setattr(adapter, "_remove_isolated_home", slow_remove)
        task = asyncio.create_task(adapter._finalize_run_resources(Client(), isolated))  # type: ignore[arg-type]
        for _ in range(100):
            if cleanup_started.is_set():
                break
            await asyncio.sleep(0.002)
        assert cleanup_started.is_set()

        started = time.perf_counter()
        await asyncio.sleep(0.02)
        assert time.perf_counter() - started < 0.1
        assert task.done() is False
        await task
        assert not isolated.exists()

    asyncio.run(scenario())


def test_codex_agent_shutdown_failure_still_cleans_credential_home(tmp_path: Path) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        adapter = CodexAppServerAdapter(policy)
        isolated = tmp_path / "runtime" / "codex_agent_homes" / "AGENT-FAULT"
        isolated.mkdir(parents=True)
        (isolated / "auth.json").write_text("test-only\n", encoding="utf-8")

        class FailingClient:
            async def close(self) -> None:
                raise PermissionError("simulated process shutdown failure")

        try:
            await adapter._finalize_run_resources(FailingClient(), isolated)  # type: ignore[arg-type]
        except AgentRuntimeUnavailableError as exc:
            assert "shutdown failed" in str(exc)
        else:
            raise AssertionError("shutdown failure was not surfaced")

        assert not isolated.exists()

        cancelled_home = tmp_path / "runtime" / "codex_agent_homes" / "AGENT-CANCELLED-CLOSE"
        cancelled_home.mkdir(parents=True)
        (cancelled_home / "auth.json").write_text("test-only\n", encoding="utf-8")

        class CancelledClient:
            async def close(self) -> None:
                raise asyncio.CancelledError()

        try:
            await adapter._finalize_run_resources(CancelledClient(), cancelled_home)  # type: ignore[arg-type]
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("close cancellation was not propagated")
        assert not cancelled_home.exists()

    asyncio.run(scenario())


def test_codex_completed_turn_keeps_result_when_credential_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "provider-default-model"\nmodel_reasoning_effort = "high"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working_dir = policy.resolve_working_dir(None, task_id="TASK-CLEANUP-AFTER-SUCCESS")
        fake_server = tmp_path / "fake_codex_cleanup_server.py"
        fake_server.write_text(_FAKE_CODEX_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_remove = adapter._remove_isolated_home
        events: list[tuple[str, dict[str, object]]] = []

        def fail_final_cleanup(path: Path) -> None:
            if path.exists() and path.name == "AS-CLEANUP-AFTER-SUCCESS":
                raise AgentRuntimeUnavailableError("simulated persistent cleanup failure")
            real_remove(path)

        monkeypatch.setattr(adapter, "_remove_isolated_home", fail_final_cleanup)

        async def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        async def wait_for_master(
            _request_id: str,
            kind: str,
            _payload: dict[str, object],
        ) -> dict[str, object]:
            if kind == "approval":
                return {"decision": "approve_once"}
            return {"answers": {"q1": ["テストを続けて"]}}

        summary = await adapter.run(
            AgentRunRequest(
                task_id="TASK-CLEANUP-AFTER-SUCCESS",
                agent_session_id="AS-CLEANUP-AFTER-SUCCESS",
                resident="Codex",
                provider="codex",
                prompt="テスト作業をして",
                working_dir=working_dir,
            ),
            emit=emit,  # type: ignore[arg-type]
            wait_for_master=wait_for_master,  # type: ignore[arg-type]
        )

        assert summary == "作業完了"
        assert any(
            event_type == "error"
            and payload.get("code") == "provider_cleanup_failed"
            for event_type, payload in events
        )
        assert adapter._runtime_owned_snapshot() == set()

    asyncio.run(scenario())


def test_codex_cancel_during_staged_quiesce_does_not_claim_unapplied_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "provider-default-model"\nmodel_reasoning_effort = "high"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        import core.agents.codex_app_server as codex_app_server

        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="TASK-CANCEL-DURING-CLEANUP")
        fake_server = tmp_path / "fake_codex_cancel_cleanup.py"
        fake_server.write_text(_FAKE_CODEX_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_terminate = codex_app_server._terminate_process_tree
        terminate_started = asyncio.Event()
        terminate_release = asyncio.Event()
        holder: dict[str, object] = {}

        async def delayed_terminate(process):
            holder["process"] = process
            terminate_started.set()
            await terminate_release.wait()
            return await real_terminate(process)

        monkeypatch.setattr(codex_app_server, "_terminate_process_tree", delayed_terminate)

        async def emit(_event_type: str, _payload: dict[str, object]) -> None:
            return None

        async def wait_for_master(
            _request_id: str,
            kind: str,
            _payload: dict[str, object],
        ) -> dict[str, object]:
            if kind == "approval":
                return {"decision": "approve_once"}
            return {"answers": {"q1": ["テストを続けて"]}}

        task = asyncio.create_task(adapter.run(
            AgentRunRequest(
                task_id="TASK-CANCEL-DURING-CLEANUP",
                agent_session_id="AS-CANCEL-DURING-CLEANUP",
                resident="Codex",
                provider="codex",
                prompt="テスト作業をして",
                working_dir=working,
            ),
            emit=emit,  # type: ignore[arg-type]
            wait_for_master=wait_for_master,  # type: ignore[arg-type]
        ))
        await asyncio.wait_for(terminate_started.wait(), timeout=2.0)
        task.cancel()
        await asyncio.sleep(0)
        terminate_release.set()

        process = holder["process"]
        home = tmp_path / "runtime" / "codex_agent_homes" / "AS-CANCEL-DURING-CLEANUP"
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=3.0)
            assert not (working / "result.txt").exists()
            assert not home.exists()
            assert adapter._runtime_owned_snapshot() == set()
        finally:
            terminate_release.set()
            if getattr(process, "returncode") is None:
                await real_terminate(process)  # type: ignore[arg-type]
            if home.exists():
                adapter._remove_isolated_home(home)

    asyncio.run(scenario())


def test_codex_cancel_intent_marked_before_active_registration_prevents_late_success_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    (source_home / "config.toml").write_text(
        'model = "provider-default-model"\nmodel_reasoning_effort = "high"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        import core.agents.codex_app_server as codex_app_server

        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="TASK-EARLY-CANCEL-INTENT")
        fake_server = tmp_path / "fake_codex_early_cancel_intent.py"
        fake_server.write_text(_FAKE_CODEX_EARLY_CANCEL_SUCCESS_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]
        real_spawn = adapter._spawn
        real_cancel_intent_requested = adapter._cancel_intent_requested
        real_terminate = codex_app_server._terminate_process_tree
        spawned = asyncio.Event()
        cancel_intent_checked = asyncio.Event()
        terminate_started = asyncio.Event()
        terminate_release = asyncio.Event()
        holder: dict[str, object] = {}

        async def tracked_spawn(command, working_dir, *, env=None):
            process = await real_spawn(command, working_dir, env=env)
            holder["process"] = process
            spawned.set()
            return process

        def tracked_cancel_intent_requested(agent_session_id: str) -> bool:
            result = real_cancel_intent_requested(agent_session_id)
            cancel_intent_checked.set()
            return result

        async def delayed_terminate(process):
            terminate_started.set()
            await terminate_release.wait()
            return await real_terminate(process)

        monkeypatch.setattr(adapter, "_spawn", tracked_spawn)
        monkeypatch.setattr(adapter, "_cancel_intent_requested", tracked_cancel_intent_requested)
        monkeypatch.setattr(codex_app_server, "_terminate_process_tree", delayed_terminate)
        await adapter._active_lock.acquire()

        async def emit(_event_type: str, _payload: dict[str, object]) -> None:
            return None

        async def wait_for_master(*_args, **_kwargs):
            return {"decision": "reject"}

        task = asyncio.create_task(adapter.run(
            AgentRunRequest(
                task_id="TASK-EARLY-CANCEL-INTENT",
                agent_session_id="AS-EARLY-CANCEL-INTENT",
                resident="Codex",
                provider="codex",
                prompt="cancel before active registration",
                working_dir=working,
            ),
            emit=emit,  # type: ignore[arg-type]
            wait_for_master=wait_for_master,  # type: ignore[arg-type]
        ))
        await asyncio.wait_for(spawned.wait(), timeout=1.0)
        await asyncio.wait_for(cancel_intent_checked.wait(), timeout=1.0)

        adapter.mark_cancel_intent("AS-EARLY-CANCEL-INTENT")
        adapter._active_lock.release()
        await asyncio.wait_for(terminate_started.wait(), timeout=2.0)
        task.cancel()
        await asyncio.sleep(0)
        terminate_release.set()

        process = holder["process"]
        home = tmp_path / "runtime" / "codex_agent_homes" / "AS-EARLY-CANCEL-INTENT"
        try:
            try:
                result = await asyncio.wait_for(task, timeout=3.0)
            except asyncio.CancelledError:
                result = None
            assert not (
                isinstance(result, AgentRunResult)
                and result.work_committed
            ), "cancel intent recorded before active registration must precede later provider success"
        finally:
            if adapter._active_lock.locked():
                adapter._active_lock.release()
            terminate_release.set()
            if getattr(process, "returncode") is None:
                await real_terminate(process)  # type: ignore[arg-type]
            if home.exists():
                adapter._remove_isolated_home(home)

    asyncio.run(scenario())


def test_codex_cancel_first_late_completed_turn_is_not_reported_as_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        working = policy.resolve_working_dir(None, task_id="TASK-CANCEL-FIRST-LATE-COMPLETE")
        fake_server = tmp_path / "fake_codex_cancel_first_late_complete.py"
        fake_server.write_text(_FAKE_CODEX_CANCEL_FIRST_LATE_COMPLETE_SERVER, encoding="utf-8")
        adapter = CodexAppServerAdapter(policy)
        adapter._resolve_command = lambda: (sys.executable, str(fake_server))  # type: ignore[method-assign]

        async def emit(_event_type: str, _payload: dict[str, object]) -> None:
            return None

        async def wait_for_master(*_args, **_kwargs):
            return {"decision": "reject"}

        run_task = asyncio.create_task(adapter.run(
            AgentRunRequest(
                task_id="TASK-CANCEL-FIRST-LATE-COMPLETE",
                agent_session_id="AS-CANCEL-FIRST-LATE-COMPLETE",
                resident="Codex",
                provider="codex",
                prompt="wait for cancel",
                working_dir=working,
            ),
            emit=emit,  # type: ignore[arg-type]
            wait_for_master=wait_for_master,
        ))
        for _ in range(100):
            active = adapter._active.get("AS-CANCEL-FIRST-LATE-COMPLETE")
            if active is not None and active.thread_id and active.turn_id:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("Codex turn did not become active")

        assert await adapter.cancel("AS-CANCEL-FIRST-LATE-COMPLETE") is True
        result = await asyncio.wait_for(run_task, timeout=2.0)

        assert not (
            isinstance(result, AgentRunResult)
            and result.work_committed
        ), "late turn/completed after cancel intent must not become committed work"

    asyncio.run(scenario())


def test_codex_cancel_records_intent_before_awaiting_provider_interrupt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        adapter = CodexAppServerAdapter(policy)
        observed: list[bool] = []
        active: SimpleNamespace

        class FakeClient:
            async def request(self, method: str, params: dict[str, object]) -> dict[str, object]:
                assert method == "turn/interrupt"
                assert params == {"threadId": "thread-1", "turnId": "turn-1"}
                observed.append(active.cancel_requested)
                await asyncio.sleep(0)
                return {}

        active = SimpleNamespace(
            client=FakeClient(),
            thread_id="thread-1",
            turn_id="turn-1",
            cancel_requested=False,
        )
        adapter._active["AS-CANCEL-INTENT"] = active  # type: ignore[assignment]

        assert await adapter.cancel("AS-CANCEL-INTENT") is True
        assert observed == [True]
        assert active.cancel_requested is True

    asyncio.run(scenario())


def test_codex_process_stop_faults_are_bounded(monkeypatch) -> None:
    import core.agents.codex_app_server as codex_app_server

    class FailingProcess:
        pid = 424242
        returncode = None

        async def wait(self):
            await asyncio.Event().wait()

        def terminate(self) -> None:
            raise PermissionError("terminate denied")

        def kill(self) -> None:
            raise PermissionError("kill denied")

    async def fail_taskkill(*args, **kwargs):
        raise OSError("taskkill unavailable")

    async def scenario() -> None:
        monkeypatch.setattr(codex_app_server.asyncio, "create_subprocess_exec", fail_taskkill)
        monkeypatch.setattr(codex_app_server, "_PROCESS_WAIT_STEP_SEC", 0.01)
        result = await asyncio.wait_for(
            _terminate_process_tree(FailingProcess()),  # type: ignore[arg-type]
            timeout=0.2,
        )
        assert result is False

        class HangingWaitProcess:
            pid = 434343
            returncode = None

            async def wait(self):
                await asyncio.Event().wait()

            def terminate(self) -> None:
                pass

            def kill(self) -> None:
                pass

        result = await asyncio.wait_for(
            _terminate_process_tree(HangingWaitProcess()),  # type: ignore[arg-type]
            timeout=0.2,
        )
        assert result is False

    asyncio.run(scenario())


def test_codex_stderr_debug_log_is_bounded_and_drops_long_line_tail(caplog, monkeypatch) -> None:
    logger = logging.getLogger("nirai.core.agent.codex")
    monkeypatch.setattr(logger, "propagate", False)

    async def scenario() -> None:
        stderr = asyncio.StreamReader()
        stderr.feed_data(b"alpha\rbeta\n")
        stderr.feed_data(("A" * 500 + "SECRET_AFTER_LIMIT" + "B" * 5000 + "\r\n").encode("utf-8"))
        stderr.feed_eof()
        client = object.__new__(_JsonLineAppServer)
        client.process = SimpleNamespace(stderr=stderr)  # type: ignore[attr-defined]

        # Core logging owns its handlers and disables root propagation.
        # Capture at the producer so this assertion is independent of test order.
        logger.addHandler(caplog.handler)
        try:
            with caplog.at_level(logging.DEBUG, logger=logger.name):
                await client._drain_stderr()
        finally:
            logger.removeHandler(caplog.handler)

        messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == "nirai.core.agent.codex"
            and record.getMessage().startswith("codex_app_server_stderr: ")
        ]
        assert len(messages) == 2
        assert messages[0].split(": ", 1)[1] == "alpha\\rbeta"
        excerpt = messages[1].split(": ", 1)[1]
        assert len(excerpt) == 500
        assert "SECRET_AFTER_LIMIT" not in excerpt

    asyncio.run(scenario())


def test_codex_agent_home_copies_only_auth(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / "source-codex-home"
    source_home.mkdir()
    (source_home / "auth.json").write_text('{"token":"test-only"}\n', encoding="utf-8")
    (source_home / "AGENTS.md").write_text("must not leak\n", encoding="utf-8")
    (source_home / "config.toml").write_text('model = "must-not-leak"\n', encoding="utf-8")
    skill_dir = source_home / "skills" / "global-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("must not leak\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(source_home))

    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    adapter = CodexAppServerAdapter(policy)
    isolated = adapter._prepare_isolated_codex_home("AGENT-HOME")
    try:
        assert sorted(path.name for path in isolated.iterdir()) == ["auth.json"]
        assert (isolated / "auth.json").read_text(encoding="utf-8") == '{"token":"test-only"}\n'
    finally:
        import shutil
        shutil.rmtree(isolated, ignore_errors=True)


_FAKE_CODEX_STAGED_MUTATION_SERVER = r'''
import json
from pathlib import Path
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


cwd = None
prompt = None
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if request_id == 920:
        if message.get("result", {}).get("decision") != "accept":
            raise SystemExit("staged command was not auto approved")
        root = Path(cwd)
        if prompt == "safe":
            (root / "safe.txt").write_text("safe from stage\n", encoding="utf-8")
        elif prompt == "delete-many":
            for path in sorted(root.glob("victim-*.txt")):
                path.unlink()
        else:
            raise SystemExit("unexpected prompt")
        send({"method": "item/completed", "params": {
            "threadId": "thread-stage", "turnId": "turn-stage",
            "item": {"id": "msg-stage", "type": "agentMessage", "text": "staged mutation complete", "phase": "final_answer"}
        }})
        send({"method": "turn/completed", "params": {
            "turn": {"id": "turn-stage", "items": [], "status": "completed", "error": None}
        }})
        continue
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        cwd = message["params"]["cwd"]
        send({"id": request_id, "result": {"thread": {"id": "thread-stage"}}})
    elif method == "turn/start" and request_id is not None:
        prompt = message["params"]["input"][0]["text"]
        send({"id": request_id, "result": {"turn": {"id": "turn-stage", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {
            "threadId": "thread-stage", "turn": {"id": "turn-stage", "items": [], "status": "inProgress"}
        }})
        send({"id": 920, "method": "item/commandExecution/requestApproval", "params": {
            "threadId": "thread-stage", "turnId": "turn-stage", "itemId": "cmd-stage",
            "command": "python -c \"import shutil; shutil.rmtree('generated')\"", "cwd": cwd,
            "reason": "exercise staged arbitrary script boundary"
        }})
'''


_FAKE_CODEX_AUTO_APPROVAL_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


cwd = None
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if request_id == 910:
        if message.get("result", {}).get("decision") != "accept":
            raise SystemExit("safe command was not auto approved")
        send({"method": "item/started", "params": {
            "threadId": "thread-auto", "turnId": "turn-auto",
            "item": {"id": "file-auto", "type": "fileChange", "status": "inProgress", "changes": [{
                "path": cwd + "\\safe.txt",
                "diff": "--- /dev/null\\n+++ b/safe.txt\\n@@ -0,0 +1 @@\\n+safe",
                "kind": "add"
            }]}
        }})
        send({"id": 911, "method": "item/fileChange/requestApproval", "params": {
            "threadId": "thread-auto", "turnId": "turn-auto", "itemId": "file-auto",
            "grantRoot": cwd, "reason": "safe workspace edit"
        }})
        continue
    if request_id == 911:
        if message.get("result", {}).get("decision") != "accept":
            raise SystemExit("safe file change was not auto approved")
        with open(cwd + "\\safe.txt", "w", encoding="utf-8") as handle:
            handle.write("safe\n")
        send({"method": "item/completed", "params": {
            "threadId": "thread-auto", "turnId": "turn-auto",
            "item": {"id": "file-auto", "type": "fileChange", "status": "completed", "changes": [{
                "path": cwd + "\\safe.txt",
                "diff": "--- /dev/null\\n+++ b/safe.txt\\n@@ -0,0 +1 @@\\n+safe",
                "kind": "add"
            }]}
        }})
        send({"method": "item/completed", "params": {
            "threadId": "thread-auto", "turnId": "turn-auto",
            "item": {"id": "msg-auto", "type": "agentMessage", "text": "auto approved", "phase": "final_answer"}
        }})
        send({"method": "turn/completed", "params": {
            "turn": {"id": "turn-auto", "items": [], "status": "completed", "error": None}
        }})
        continue
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        cwd = message["params"]["cwd"]
        send({"id": request_id, "result": {"thread": {"id": "thread-auto"}}})
    elif method == "turn/start" and request_id is not None:
        send({"id": request_id, "result": {"turn": {"id": "turn-auto", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {
            "threadId": "thread-auto", "turn": {"id": "turn-auto", "items": [], "status": "inProgress"}
        }})
        send({"id": 910, "method": "item/commandExecution/requestApproval", "params": {
            "threadId": "thread-auto", "turnId": "turn-auto", "itemId": "cmd-auto",
            "command": "python -m pytest", "cwd": cwd, "reason": "safe local test"
        }})
'''


_FAKE_CODEX_EARLY_CANCEL_SUCCESS_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        send({"id": request_id, "result": {"thread": {"id": "thread-early-cancel"}}})
    elif method == "turn/start" and request_id is not None:
        send({"id": request_id, "result": {"turn": {"id": "turn-early-cancel", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": "thread-early-cancel", "turn": {"id": "turn-early-cancel", "items": [], "status": "inProgress"}}})
        send({"method": "item/completed", "params": {"threadId": "thread-early-cancel", "turnId": "turn-early-cancel", "item": {"id": "msg-early-cancel", "type": "agentMessage", "text": "done", "phase": "final_answer"}}})
        send({"method": "turn/completed", "params": {"turn": {"id": "turn-early-cancel", "items": [], "status": "completed", "error": None}}})
'''


_FAKE_CODEX_CANCEL_FIRST_LATE_COMPLETE_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        send({"id": request_id, "result": {"thread": {"id": "thread-cancel-first"}}})
    elif method == "turn/start" and request_id is not None:
        send({"id": request_id, "result": {"turn": {"id": "turn-cancel-first", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": "thread-cancel-first", "turn": {"id": "turn-cancel-first", "items": [], "status": "inProgress"}}})
    elif method == "turn/interrupt" and request_id is not None:
        # The cancel RPC has already been issued. Provider completion arriving
        # now is late and must not be reclassified as pre-cancel committed work.
        send({"method": "turn/completed", "params": {"turn": {"id": "turn-cancel-first", "items": [], "status": "completed", "error": None}}})
        send({"id": request_id, "result": {}})
'''


_FAKE_CODEX_USAGE_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "account/rateLimits/read" and request_id is not None:
        send({"id": request_id, "result": {"rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 20, "windowDurationMins": 300, "resetsAt": 1800000000},
            "secondary": {"usedPercent": 30, "windowDurationMins": 10080, "resetsAt": 1800604800},
            "rateLimitReachedType": None
        }}})
'''


_FAKE_CODEX_SERVER = r'''
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")


def send(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


cwd = None
stage = "normal"
for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    if request_id == 900:
        if message.get("result", {}).get("decision") != "accept":
            send({"method": "turn/completed", "params": {"turn": {"id": "turn-1", "items": [], "status": "failed", "error": {"message": "approval mapping failed"}}}})
            continue
        send({"id": 901, "method": "item/tool/requestUserInput", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "question-1", "questions": [{"id": "q1", "header": "確認", "question": "続ける？", "isSecret": True, "options": []}]}})
        continue
    if request_id == 901:
        answers = message.get("result", {}).get("answers", {})
        if answers.get("q1", {}).get("answers") != ["テストを続けて"]:
            send({"method": "turn/completed", "params": {"turn": {"id": "turn-1", "items": [], "status": "failed", "error": {"message": "question mapping failed"}}}})
            continue
        with open(cwd + "\\result.txt", "w", encoding="utf-8") as handle:
            handle.write("done\n")
        send({"method": "item/started", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "file-1", "type": "fileChange", "status": "inProgress", "changes": [{"path": cwd + "\\result.txt", "diff": "+done", "kind": {"type": "add"}}]}}})
        send({"method": "item/completed", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "file-1", "type": "fileChange", "status": "completed", "changes": [{"path": cwd + "\\result.txt", "diff": "+done", "kind": {"type": "add"}}]}}})
        send({"method": "turn/diff/updated", "params": {"threadId": "thread-1", "turnId": "turn-1", "diff": "+done"}})
        send({"method": "turn/plan/updated", "params": {"threadId": "thread-1", "turnId": "turn-1", "explanation": "done", "plan": [{"step": "実装", "status": "completed"}]}})
        send({"method": "item/completed", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "reason-1", "type": "reasoning", "content": ["private chain"], "summary": []}}})
        send({"method": "item/completed", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "msg-1", "type": "agentMessage", "text": "作業完了", "phase": "final_answer"}}})
        send({"method": "turn/completed", "params": {"turn": {"id": "turn-1", "items": [], "status": "completed", "error": None}}})
        continue
    if method == "initialize" and request_id is not None:
        send({"id": request_id, "result": {"userAgent": "fake"}})
    elif method == "initialized":
        pass
    elif method == "thread/start" and request_id is not None:
        cwd = message["params"]["cwd"]
        if message["params"].get("approvalPolicy") != "untrusted" or message["params"].get("approvalsReviewer") != "user":
            send({"id": request_id, "error": {"code": -32001, "message": "unsafe thread approval policy"}})
            continue
        send({"id": request_id, "result": {"thread": {"id": "thread-1"}}})
    elif method == "turn/start" and request_id is not None:
        sandbox = message["params"].get("sandboxPolicy", {})
        if message["params"].get("approvalPolicy") != "untrusted" or message["params"].get("approvalsReviewer") != "user" or sandbox.get("networkAccess") is not False or sandbox.get("writableRoots") != [cwd]:
            send({"id": request_id, "error": {"code": -32002, "message": "unsafe turn policy"}})
            continue
        if message["params"].get("model") != "provider-default-model" or message["params"].get("effort") != "high":
            send({"id": request_id, "error": {"code": -32003, "message": "provider default model/reasoning was not inherited"}})
            continue
        send({"id": request_id, "result": {"turn": {"id": "turn-1", "items": [], "status": "inProgress"}}})
        send({"method": "turn/started", "params": {"threadId": "thread-1", "turn": {"id": "turn-1", "items": [], "status": "inProgress"}}})
        send({"method": "item/started", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "cmd-1", "type": "commandExecution", "command": "python -m pytest", "cwd": cwd, "status": "inProgress", "commandActions": []}}})
        send({"method": "item/commandExecution/outputDelta", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "cmd-1", "delta": "1 passed\n"}})
        send({"method": "item/completed", "params": {"threadId": "thread-1", "turnId": "turn-1", "item": {"id": "cmd-1", "type": "commandExecution", "command": "python -m pytest", "cwd": cwd, "status": "completed", "commandActions": [], "aggregatedOutput": "1 passed", "exitCode": 0, "durationMs": 10}}})
        send({"id": 900, "method": "item/commandExecution/requestApproval", "params": {"threadId": "thread-1", "turnId": "turn-1", "itemId": "approve-1", "startedAtMs": 1, "command": "git push", "cwd": cwd, "reason": "external write"}})
'''
