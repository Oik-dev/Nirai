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
    AgentRunRequest,
    AgentRuntimeUnavailableError,
    AgentWorkspacePolicy,
    CodexAppServerAdapter,
)
from core.agents.codex_app_server import (
    _JsonLineAppServer,
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


def test_codex_stderr_debug_log_is_bounded_and_drops_long_line_tail(caplog) -> None:
    async def scenario() -> None:
        stderr = asyncio.StreamReader()
        stderr.feed_data(b"alpha\rbeta\n")
        stderr.feed_data(("A" * 500 + "SECRET_AFTER_LIMIT" + "B" * 5000 + "\r\n").encode("utf-8"))
        stderr.feed_eof()
        client = object.__new__(_JsonLineAppServer)
        client.process = SimpleNamespace(stderr=stderr)  # type: ignore[attr-defined]

        with caplog.at_level(logging.DEBUG, logger="nirai.core.agent.codex"):
            await client._drain_stderr()

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
