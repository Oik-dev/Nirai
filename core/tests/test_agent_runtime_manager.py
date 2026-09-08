from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import core.agents.manager as manager_module
from core.agents import (
    AgentResourceBusyError,
    AgentRunRequest,
    AgentRuntimeError,
    AgentSafetyError,
    AgentRuntimeManager,
    AgentRuntimeManagerError,
    AgentSessionSnapshot,
    AgentSessionStore,
)
from core.agents.types import utc_now_iso


def test_default_provider_adapters_are_lazy_optional_dependencies(tmp_path: Path, monkeypatch) -> None:
    class UnavailableAdapter:
        capabilities = frozenset({"test_capability"})

        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("provider dependency unavailable")

    monkeypatch.setattr(manager_module, "CodexAppServerAdapter", UnavailableAdapter)
    monkeypatch.setattr(manager_module, "CursorAcpAdapter", UnavailableAdapter)
    monkeypatch.setattr(manager_module, "AntigravityAgentAdapter", UnavailableAdapter)

    manager = AgentRuntimeManager(tmp_path, ("runtime\\workspace",))

    assert manager._adapters == {}
    assert manager.supports_provider("codex") is True
    assert manager.supports_provider("cursor") is True
    assert manager.supports_provider("gemini") is True
    assert manager.provider_capabilities("codex") == frozenset({"test_capability"})


class _InteractiveFakeAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {
            "state": "running",
            "provider_session_id": "thread-1",
            "provider_turn_id": "turn-1",
        })
        await emit("approval_request", {
            "request_id": "approve-1",
            "kind": "command",
            "title": "Run test",
        })
        approval = await wait_for_master("approve-1", "approval", {})
        assert approval == {"decision": "approve_once"}
        await emit("question_request", {
            "request_id": "question-1",
            "questions": [{"id": "q1", "question": "Continue?"}],
        })
        question = await wait_for_master("question-1", "question", {})
        assert question == {"answers": {"q1": ["yes"]}}
        await emit("plan", {
            "request_id": "plan-1",
            "markdown": "Run the tests",
            "approval_required": True,
        })
        plan = await wait_for_master("plan-1", "plan", {})
        assert plan == {"decision": "approve"}
        await emit("command_execution", {
            "command": "python -m pytest",
            "cwd": str(request.working_dir),
            "status": "completed",
            "exit_code": 0,
        })
        return "done"

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        return True


class _HangingCancelAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancel_started: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        await asyncio.Event().wait()
        return None

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancel_started.append(agent_session_id)
        await asyncio.Event().wait()
        return True


class _ApprovalHangingCancelAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancel_started = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        await emit("approval_request", {
            "request_id": "approve-race",
            "kind": "file_change",
            "title": "Apply staged changes",
        })
        await wait_for_master("approve-race", "approval", {})
        return "unexpected"

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancel_started.set()
        await asyncio.Event().wait()
        return True


class _ApprovalDeliveryAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.waiting = asyncio.Event()
        self.delivered = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        await emit("approval_request", {
            "request_id": "approve-delivery-race",
            "kind": "file_change",
            "title": "Apply staged changes",
        })
        self.waiting.set()
        await wait_for_master("approve-delivery-race", "approval", {})
        self.delivered.set()
        return "unexpected"

    async def cancel(self, agent_session_id: str) -> bool:
        return True


class _RecoveryCaptureAdapter:
    provider = "codex"
    capabilities = frozenset({"crash_resume"})

    def __init__(self) -> None:
        self.requests: list[AgentRunRequest] = []

    async def run(self, request, *, emit, wait_for_master):
        self.requests.append(request)
        await emit("run_state", {
            "state": "running",
            "provider_session_id": request.provider_session_id or "thread-fresh",
        })
        return "recovered"

    async def cancel(self, agent_session_id: str) -> bool:
        return True


class _ReadOnlyCaptureAdapter:
    provider = "cursor"

    def __init__(self) -> None:
        self.request: AgentRunRequest | None = None

    async def run(self, request, *, emit, wait_for_master):
        self.request = request
        await emit("run_state", {"state": "running"})
        return "SAFE\nRead-only review completed"

    async def cancel(self, agent_session_id: str) -> bool:
        return True


class _StartBlockingAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        self.started.set()
        await emit("run_state", {"state": "running"})
        await self.release.wait()
        return "done"

    async def cancel(self, agent_session_id: str) -> bool:
        self.release.set()
        return True


class _BlockingFakeAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        await asyncio.Event().wait()
        return None

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        return True


class _CleanupFailingAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        try:
            await asyncio.Event().wait()
        finally:
            raise AgentRuntimeError("Codex credential home cleanup failed")

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        return True


class _SlowCleanupAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.cancelled: list[str] = []
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_completed = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cleanup_started.set()
            await self.cleanup_release.wait()
            self.cleanup_completed.set()
            raise

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        return True


def test_agent_runtime_manager_undeclared_adapter_capabilities_fail_closed(tmp_path: Path) -> None:
    manager = AgentRuntimeManager(
        tmp_path,
        ("runtime\\workspace",),
        adapters={"codex": _InteractiveFakeAdapter()},
    )
    assert manager.supports_provider("codex") is True
    assert manager.provider_capabilities("codex") == frozenset()


async def _wait_for_state(
    manager: AgentRuntimeManager,
    agent_session_id: str,
    state: str,
) -> dict[str, Any]:
    for _ in range(100):
        payload = manager.snapshot_payload(agent_session_id)
        if payload["session"]["run_state"] == state:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(f"Agent Session did not reach {state}")


def test_agent_runtime_manager_persists_blocking_requests_and_resumes(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _InteractiveFakeAdapter()
        broadcast_events: list[dict[str, Any]] = []

        async def broadcast(event) -> None:
            broadcast_events.append(event.to_protocol())

        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            broadcast=broadcast,
        )
        snapshot = await manager.start_session(
            task_id="TASK-MANAGER",
            resident="Codex",
            provider="codex",
            prompt="do work",
        )

        assert (tmp_path / "runtime" / "workspace" / "TASK-MANAGER" / "task.md").read_text(encoding="utf-8") == "do work\n"

        approval_wait = await _wait_for_state(manager, snapshot.agent_session_id, "waiting_for_master")
        assert approval_wait["session"]["pending_request_id"] == "approve-1"
        assert approval_wait["session"]["pending_request_kind"] == "approval"
        assert await manager.respond(
            snapshot.agent_session_id,
            "approve-1",
            "approval",
            {"decision": "approve_once"},
        ) is True
        assert await manager.respond(
            snapshot.agent_session_id,
            "approve-1",
            "approval",
            {"decision": "approve_once"},
        ) is False

        question_wait = await _wait_for_state(manager, snapshot.agent_session_id, "waiting_for_master")
        assert question_wait["session"]["pending_request_id"] == "question-1"
        assert question_wait["session"]["pending_request_kind"] == "question"
        assert await manager.respond(
            snapshot.agent_session_id,
            "question-1",
            "question",
            {"answers": {"q1": ["yes"]}},
        ) is True

        plan_wait = await _wait_for_state(manager, snapshot.agent_session_id, "waiting_for_master")
        assert plan_wait["session"]["pending_request_id"] == "plan-1"
        assert plan_wait["session"]["pending_request_kind"] == "plan"
        assert await manager.respond(
            snapshot.agent_session_id,
            "plan-1",
            "plan",
            {"decision": "approve"},
        ) is True

        completed = await _wait_for_state(manager, snapshot.agent_session_id, "completed")
        assert completed["session"]["final_summary"] == "done"
        assert completed["session"]["provider_session_id"] == "thread-1"
        assert completed["session"]["provider_turn_id"] == "turn-1"
        assert completed["events"][0]["event_id"].startswith("AE-AS-")
        assert all("provider_session_id" not in event["payload"] for event in completed["events"])
        assert all("provider_turn_id" not in event["payload"] for event in completed["events"])
        states = [
            event["payload"].get("state")
            for event in completed["events"]
            if event["type"] == "run_state"
        ]
        assert states == [
            "starting",
            "running",
            "waiting_for_master",
            "running",
            "waiting_for_master",
            "running",
            "waiting_for_master",
            "running",
            "completed",
        ]
        assert any(event["type"] == "command_execution" for event in broadcast_events)

    asyncio.run(scenario())


def test_agent_runtime_manager_allows_independent_workspace_sessions_concurrently(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            max_concurrent_sessions=2,
        )
        first = await manager.start_session(
            task_id="TASK-CONCURRENT-A",
            resident="CodexA",
            provider="codex",
            prompt="work A",
        )
        second = await manager.start_session(
            task_id="TASK-CONCURRENT-B",
            resident="CodexB",
            provider="codex",
            prompt="work B",
        )
        await _wait_for_state(manager, first.agent_session_id, "running")
        await _wait_for_state(manager, second.agent_session_id, "running")
        assert manager.has_active_session() is True

        with pytest.raises(AgentResourceBusyError, match="concurrency budget"):
            await manager.start_session(
                task_id="TASK-CONCURRENT-C",
                resident="CodexC",
                provider="codex",
                prompt="work C",
            )

        adapter.release.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")
        await _wait_for_state(manager, second.agent_session_id, "completed")

    asyncio.run(scenario())


def test_agent_runtime_manager_does_not_double_count_a_materialized_start_slot(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            max_concurrent_sessions=2,
        )
        original_record_event = manager._record_event
        first_start_event = asyncio.Event()
        release_first_start_event = asyncio.Event()
        starting_calls = 0

        async def delayed_record_event(agent_session_id, event_type, payload):
            nonlocal starting_calls
            if event_type == "run_state" and payload.get("state") == "starting":
                starting_calls += 1
                if starting_calls == 1:
                    first_start_event.set()
                    await release_first_start_event.wait()
            return await original_record_event(agent_session_id, event_type, payload)

        manager._record_event = delayed_record_event  # type: ignore[method-assign]
        first_task = asyncio.create_task(manager.start_session(
            task_id="TASK-START-SLOT-A",
            resident="CodexA",
            provider="codex",
            prompt="work A",
        ))
        await asyncio.wait_for(first_start_event.wait(), timeout=0.5)

        second = await asyncio.wait_for(manager.start_session(
            task_id="TASK-START-SLOT-B",
            resident="CodexB",
            provider="codex",
            prompt="work B",
        ), timeout=0.5)
        release_first_start_event.set()
        first = await asyncio.wait_for(first_task, timeout=0.5)

        assert first.agent_session_id != second.agent_session_id
        adapter.release.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")
        await _wait_for_state(manager, second.agent_session_id, "completed")

    asyncio.run(scenario())


def test_agent_runtime_manager_serializes_write_sessions_for_same_workspace(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
            max_concurrent_sessions=4,
        )
        first = await manager.start_session(
            task_id="TASK-WRITE-A",
            resident="CodexA",
            provider="codex",
            prompt="write A",
            working_dir=str(project),
        )
        await _wait_for_state(manager, first.agent_session_id, "running")

        with pytest.raises(AgentResourceBusyError, match="same workspace"):
            await manager.start_session(
                task_id="TASK-WRITE-B",
                resident="CodexB",
                provider="codex",
                prompt="write B",
                working_dir=str(project),
            )

        adapter.release.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")

    asyncio.run(scenario())


def test_agent_runtime_manager_keeps_task_metadata_outside_named_project_working_dir(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
        )
        metadata_dir = manager.workspace_policy.resolve_working_dir(None, task_id="TASK-PROJECT")
        snapshot = await manager.start_session(
            task_id="TASK-PROJECT",
            resident="Codex",
            provider="codex",
            prompt="edit project",
            working_dir=str(project),
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)

        assert snapshot.working_dir == str(project.resolve())
        assert (metadata_dir / "task.md").read_text(encoding="utf-8") == "edit project\n"
        assert (project / "task.md").exists() is False

        adapter.release.set()
        await _wait_for_state(manager, snapshot.agent_session_id, "completed")

    asyncio.run(scenario())


def test_agent_runtime_manager_propagates_read_only_review_without_granting_normal_root_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        (tmp_path / "core").mkdir()
        (tmp_path / "world").mkdir()
        adapter = _ReadOnlyCaptureAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"cursor": adapter},
        )

        snapshot = await manager.start_session(
            task_id="HR-MANAGER-REVIEW",
            resident="Holo",
            provider="cursor",
            prompt="review current implementation",
            working_dir=str(tmp_path),
            read_only=True,
        )
        completed = await _wait_for_state(manager, snapshot.agent_session_id, "completed")

        assert adapter.request is not None
        assert adapter.request.read_only is True
        assert adapter.request.working_dir == tmp_path.resolve()
        assert completed["session"]["final_summary"] == "SAFE\nRead-only review completed"
        assert (tmp_path / "runtime" / "workspace" / "HR-MANAGER-REVIEW" / "task.md").is_file()

        with pytest.raises(AgentSafetyError, match="outside tasks.allowed_dirs|M5"):
            await manager.start_session(
                task_id="TASK-NORMAL-ROOT",
                resident="Holo",
                provider="cursor",
                prompt="must not write Nirai root",
                working_dir=str(tmp_path),
                read_only=False,
            )

    asyncio.run(scenario())


def test_agent_runtime_manager_never_recreates_explicit_external_working_dir_that_disappeared(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
        )

        project.rmdir()
        with pytest.raises(AgentSafetyError, match="working directory does not exist"):
            await manager.start_session(
                task_id="TASK-MISSING-EXTERNAL",
                resident="Codex",
                provider="codex",
                prompt="must not recreate deleted project",
                working_dir=str(project),
            )

        assert project.exists() is False
        assert adapter.started.is_set() is False
        assert manager.list_snapshots() == []

    asyncio.run(scenario())


def test_agent_runtime_manager_rejects_external_task_metadata_directory(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
        )

        with pytest.raises(AgentSafetyError, match="metadata directory"):
            await manager.start_session(
                task_id="TASK-BAD-METADATA",
                resident="Codex",
                provider="codex",
                prompt="must stay internal",
                working_dir=str(project),
                task_metadata_dir=str(project),
            )

        assert adapter.started.is_set() is False
        assert (project / "task.md").exists() is False

    asyncio.run(scenario())


def test_agent_runtime_manager_response_is_superseded_cleanly_by_cancel_during_resume_broadcast(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ApprovalDeliveryAdapter()
        resume_broadcast_started = asyncio.Event()
        release_resume_broadcast = asyncio.Event()

        async def broadcast(event) -> None:
            if (
                event.type == "run_state"
                and event.payload.get("resumed_from") == "approval"
                and event.payload.get("request_id") == "approve-delivery-race"
            ):
                resume_broadcast_started.set()
                await release_resume_broadcast.wait()

        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            broadcast=broadcast,
        )
        snapshot = await manager.start_session(
            task_id="TASK-DELIVERY-RACE",
            resident="Codex",
            provider="codex",
            prompt="do work",
        )
        waiting = await _wait_for_state(manager, snapshot.agent_session_id, "waiting_for_master")
        assert waiting["session"]["pending_request_id"] == "approve-delivery-race"

        response_task = asyncio.create_task(manager.respond(
            snapshot.agent_session_id,
            "approve-delivery-race",
            "approval",
            {"decision": "approve_once"},
        ))
        await asyncio.wait_for(resume_broadcast_started.wait(), timeout=0.5)

        assert await manager.cancel(snapshot.agent_session_id) is True
        for _ in range(50):
            pending = manager._pending.get((snapshot.agent_session_id, "approve-delivery-race"))
            if pending is None or pending[1].cancelled():
                break
            await asyncio.sleep(0.01)

        release_resume_broadcast.set()
        assert await asyncio.wait_for(response_task, timeout=0.5) is False
        assert adapter.delivered.is_set() is False
        await _wait_for_state(manager, snapshot.agent_session_id, "cancelled")

    asyncio.run(scenario())


def test_agent_runtime_manager_accepts_response_during_request_broadcast_before_provider_wait(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _InteractiveFakeAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )

        async def broadcast(event) -> None:
            if event.type == "approval_request":
                assert await manager.respond(
                    event.agent_session_id,
                    event.payload["request_id"],
                    "approval",
                    {"decision": "approve_once"},
                ) is True
            elif event.type == "question_request":
                assert await manager.respond(
                    event.agent_session_id,
                    event.payload["request_id"],
                    "question",
                    {"answers": {"q1": ["yes"]}},
                ) is True
            elif event.type == "plan":
                assert await manager.respond(
                    event.agent_session_id,
                    event.payload["request_id"],
                    "plan",
                    {"decision": "approve"},
                ) is True

        manager.set_broadcast(broadcast)
        snapshot = await manager.start_session(
            task_id="TASK-EARLY-RESPONSE",
            resident="Codex",
            provider="codex",
            prompt="do work",
        )
        completed = await _wait_for_state(manager, snapshot.agent_session_id, "completed")
        assert completed["session"]["final_summary"] == "done"
        assert not any(key[0] == snapshot.agent_session_id for key in manager._pending)

    asyncio.run(scenario())


def test_agent_runtime_manager_accepts_second_session_when_workspace_is_independent(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )
        first = await manager.start_session(
            task_id="TASK-FIRST",
            resident="Codex",
            provider="codex",
            prompt="first",
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)

        second = await manager.start_session(
            task_id="TASK-SECOND",
            resident="Codex",
            provider="codex",
            prompt="second",
        )

        snapshot_ids = {snapshot.agent_session_id for snapshot in manager.list_snapshots()}
        assert first.agent_session_id in snapshot_ids
        assert second.agent_session_id in snapshot_ids
        adapter.release.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")
        await _wait_for_state(manager, second.agent_session_id, "completed")

    asyncio.run(scenario())


def test_agent_runtime_manager_stop_waits_for_inflight_start_and_prevents_provider_launch(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _StartBlockingAdapter()
        starting_broadcast = asyncio.Event()
        release_starting_broadcast = asyncio.Event()

        async def broadcast(event) -> None:
            if event.type == "run_state" and event.payload.get("state") == "starting":
                starting_broadcast.set()
                await release_starting_broadcast.wait()

        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            broadcast=broadcast,
        )
        start_task = asyncio.create_task(manager.start_session(
            task_id="TASK-STOP-DURING-START",
            resident="Codex",
            provider="codex",
            prompt="must not launch after stop",
        ))
        await asyncio.wait_for(starting_broadcast.wait(), timeout=0.5)

        stop_task = asyncio.create_task(manager.stop())
        for _ in range(50):
            if manager._stopping:
                break
            await asyncio.sleep(0.01)
        assert manager._stopping is True
        assert stop_task.done() is False

        release_starting_broadcast.set()
        try:
            await start_task
        except AgentRuntimeManagerError as exc:
            assert "stopping" in str(exc)
        else:
            raise AssertionError("Agent Session started after Runtime stop began")
        await asyncio.wait_for(stop_task, timeout=0.5)

        snapshots = manager.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].run_state == "cancelled"
        assert adapter.started.is_set() is False
        assert manager._tasks == {}

        try:
            await manager.start_session(
                task_id="TASK-AFTER-STOP",
                resident="Codex",
                provider="codex",
                prompt="must be rejected",
            )
        except AgentRuntimeManagerError as exc:
            assert "stopping" in str(exc)
        else:
            raise AssertionError("Agent Runtime accepted a new Session after stop")

    asyncio.run(scenario())


def test_agent_runtime_manager_cancel_uses_provider_and_finishes_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _BlockingFakeAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )
        snapshot = await manager.start_session(
            task_id="TASK-CANCEL",
            resident="Codex",
            provider="codex",
            prompt="wait",
        )
        await _wait_for_state(manager, snapshot.agent_session_id, "running")

        assert await manager.cancel(snapshot.agent_session_id) is True
        cancelled = await _wait_for_state(manager, snapshot.agent_session_id, "cancelled")

        assert adapter.cancelled == [snapshot.agent_session_id]
        assert cancelled["session"]["run_state"] == "cancelled"
        assert await manager.cancel(snapshot.agent_session_id) is False

    asyncio.run(scenario())


def test_agent_runtime_manager_rejects_stale_master_response_after_cancel_begins(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ApprovalHangingCancelAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            interrupt_timeout_sec=0.05,
        )
        snapshot = await manager.start_session(
            task_id="TASK-APPROVAL-CANCEL-RACE",
            resident="Codex",
            provider="codex",
            prompt="wait for approval",
        )
        waiting = await _wait_for_state(manager, snapshot.agent_session_id, "waiting_for_master")
        assert waiting["session"]["pending_request_id"] == "approve-race"

        cancel_task = asyncio.create_task(manager.cancel(snapshot.agent_session_id))
        await asyncio.wait_for(adapter.cancel_started.wait(), timeout=0.5)
        cancelling = manager.snapshot_payload(snapshot.agent_session_id)
        assert cancelling["session"]["run_state"] == "cancelling"
        assert cancelling["session"]["pending_request_id"] == "approve-race"

        assert await manager.respond(
            snapshot.agent_session_id,
            "approve-race",
            "approval",
            {"decision": "approve_once"},
        ) is False
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "cancelling"

        assert await asyncio.wait_for(cancel_task, timeout=0.2) is True
        cancelled = await _wait_for_state(manager, snapshot.agent_session_id, "cancelled")
        assert cancelled["session"]["pending_request_id"] is None
        assert cancelled["session"]["pending_request_kind"] is None

    asyncio.run(scenario())


def test_agent_runtime_manager_second_cancel_is_idempotent_during_provider_cleanup(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _SlowCleanupAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )
        snapshot = await manager.start_session(
            task_id="TASK-DOUBLE-CANCEL",
            resident="Codex",
            provider="codex",
            prompt="wait",
        )
        await _wait_for_state(manager, snapshot.agent_session_id, "running")

        assert await manager.cancel(snapshot.agent_session_id) is True
        await asyncio.wait_for(adapter.cleanup_started.wait(), timeout=0.5)
        cancelling = manager.snapshot_payload(snapshot.agent_session_id)
        assert cancelling["session"]["run_state"] == "cancelling"
        assert snapshot.agent_session_id in manager._tasks
        assert manager._tasks[snapshot.agent_session_id].done() is False

        assert await manager.cancel(snapshot.agent_session_id) is False
        assert adapter.cancelled == [snapshot.agent_session_id]
        assert snapshot.agent_session_id in manager._tasks
        assert manager._tasks[snapshot.agent_session_id].done() is False

        adapter.cleanup_release.set()
        cancelled = await _wait_for_state(manager, snapshot.agent_session_id, "cancelled")
        assert cancelled["session"]["run_state"] == "cancelled"

    asyncio.run(scenario())


def test_agent_runtime_manager_cancel_marks_failed_when_provider_cleanup_fails(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _CleanupFailingAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )
        snapshot = await manager.start_session(
            task_id="TASK-CLEANUP-FAIL",
            resident="Codex",
            provider="codex",
            prompt="wait",
        )
        await _wait_for_state(manager, snapshot.agent_session_id, "running")

        assert await manager.cancel(snapshot.agent_session_id) is True
        failed = await _wait_for_state(manager, snapshot.agent_session_id, "failed")
        cleanup_errors = [
            event for event in failed["events"]
            if event["type"] == "error"
            and event["payload"].get("code") == "provider_cleanup_failed"
        ]

        assert adapter.cancelled == [snapshot.agent_session_id]
        assert cleanup_errors
        assert "credential home cleanup failed" in cleanup_errors[-1]["payload"]["message"]
        assert failed["session"]["run_state"] == "failed"

    asyncio.run(scenario())


def test_agent_runtime_manager_cancel_does_not_hang_when_provider_interrupt_is_unresponsive(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _HangingCancelAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            interrupt_timeout_sec=0.02,
        )
        snapshot = await manager.start_session(
            task_id="TASK-HANGING-CANCEL",
            resident="Codex",
            provider="codex",
            prompt="wait",
        )
        await _wait_for_state(manager, snapshot.agent_session_id, "running")

        assert await asyncio.wait_for(manager.cancel(snapshot.agent_session_id), timeout=0.2) is True
        cancelled = await _wait_for_state(manager, snapshot.agent_session_id, "cancelled")

        assert adapter.cancel_started == [snapshot.agent_session_id]
        assert cancelled["session"]["run_state"] == "cancelled"

    asyncio.run(scenario())


def test_agent_runtime_manager_timeout_finishes_failed(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _BlockingFakeAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            session_timeout_sec=0.05,
        )
        snapshot = await manager.start_session(
            task_id="TASK-TIMEOUT",
            resident="Codex",
            provider="codex",
            prompt="wait forever",
        )

        failed = await _wait_for_state(manager, snapshot.agent_session_id, "failed")
        timeout_events = [
            event for event in failed["events"]
            if event["type"] == "error" and event["payload"].get("code") == "session_timeout"
        ]

        assert timeout_events
        assert failed["session"]["final_summary"] is None
        assert adapter.cancelled == [snapshot.agent_session_id]

    asyncio.run(scenario())


def test_agent_runtime_manager_timeout_preserves_provider_cleanup_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _CleanupFailingAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            session_timeout_sec=0.05,
        )
        snapshot = await manager.start_session(
            task_id="TASK-TIMEOUT-CLEANUP-FAIL",
            resident="Codex",
            provider="codex",
            prompt="wait forever",
        )

        failed = await _wait_for_state(manager, snapshot.agent_session_id, "failed")
        error_codes = [
            event["payload"].get("code")
            for event in failed["events"]
            if event["type"] == "error"
        ]

        assert "session_timeout" in error_codes
        assert "provider_cleanup_failed" in error_codes
        assert adapter.cancelled == [snapshot.agent_session_id]

    asyncio.run(scenario())


def test_agent_runtime_manager_timeout_cleanup_rejects_user_cancel_and_finishes_failed(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _SlowCleanupAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            session_timeout_sec=0.02,
        )
        snapshot = await manager.start_session(
            task_id="TASK-TIMEOUT-CANCEL-RACE",
            resident="Codex",
            provider="codex",
            prompt="wait forever",
        )

        await asyncio.wait_for(adapter.cleanup_started.wait(), timeout=0.5)
        during_cleanup = manager.snapshot_payload(snapshot.agent_session_id)
        assert during_cleanup["session"]["run_state"] == "cancelling"
        assert any(
            event["type"] == "error"
            and event["payload"].get("code") == "session_timeout"
            for event in during_cleanup["events"]
        )

        assert await manager.cancel(snapshot.agent_session_id) is False
        assert adapter.cancelled == [snapshot.agent_session_id]
        assert adapter.cleanup_completed.is_set() is False

        adapter.cleanup_release.set()
        failed = await _wait_for_state(manager, snapshot.agent_session_id, "failed")
        assert adapter.cleanup_completed.is_set() is True
        assert adapter.cancelled == [snapshot.agent_session_id]
        assert failed["session"]["run_state"] == "failed"
        assert any(
            event["type"] == "error"
            and event["payload"].get("code") == "session_timeout"
            for event in failed["events"]
        )

    asyncio.run(scenario())


def test_agent_runtime_manager_bounds_large_event_payload_and_final_summary(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _InteractiveFakeAdapter()},
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id="TASK-LARGE",
            agent_session_id="AS-LARGE",
            resident="Codex",
            provider="codex",
            working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-LARGE"),
            run_state="running",
            started_at=now,
            updated_at=now,
        )
        manager.store.create(snapshot)
        manager._snapshots[snapshot.agent_session_id] = snapshot

        event = await manager._record_event(
            snapshot.agent_session_id,
            "diff",
            {"diff": "x" * 100_000, "nested": {"items": ["y" * 5_000] * 100}},
        )
        assert len(event.payload["diff"]) <= 12_000
        serialized = str(event.payload)
        assert len(serialized) < 40_000

        manager._event_payload_chars[snapshot.agent_session_id] = 2_000_000
        capped = await manager._record_event(
            snapshot.agent_session_id,
            "command_execution",
            {"output": "should-not-be-persisted" * 1000},
        )
        assert capped.payload["truncated"] is True
        assert "payload budget" in capped.payload["message"]
        count_after_sentinel = len(manager.store.read_events(snapshot.agent_session_id))
        suppressed = await manager._record_event(
            snapshot.agent_session_id,
            "status_message",
            {"text": "this ordinary detail must not grow the exhausted log"},
        )
        assert suppressed.seq == capped.seq
        assert len(manager.store.read_events(snapshot.agent_session_id)) == count_after_sentinel

        await manager._finish_session(snapshot.agent_session_id, "completed", "z" * 20_000)
        finished = manager.snapshot_payload(snapshot.agent_session_id)["session"]
        assert len(finished["final_summary"]) <= 8_000
        assert finished["final_summary"].endswith("…")

    asyncio.run(scenario())


def test_agent_runtime_manager_preserves_pending_file_change_manifest_near_session_budget(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _InteractiveFakeAdapter()},
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id="TASK-SAFETY-MANIFEST",
            agent_session_id="AS-SAFETY-MANIFEST",
            resident="Cursor",
            provider="cursor",
            working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-SAFETY-MANIFEST"),
            run_state="running",
            started_at=now,
            updated_at=now,
        )
        manager.store.create(snapshot)
        manager._snapshots[snapshot.agent_session_id] = snapshot
        manager._event_payload_chars[snapshot.agent_session_id] = 1_996_000

        changes = [
            {
                "path": "D:/workspace/" + ("x" * 180) + f"-{index}.txt",
                "relative_path": ("x" * 180) + f"-{index}.txt",
                "change_type": "modify",
            }
            for index in range(20)
        ]
        event = await manager._record_event(
            snapshot.agent_session_id,
            "file_change",
            {
                "operation_id": "cursor-stage-apply-AS-SAFETY-MANIFEST",
                "phase": "staged",
                "status": "pending_approval",
                "changes": changes,
            },
        )

        assert event.payload.get("truncated") is not True
        assert len(event.payload["changes"]) == len(changes)
        assert [item["relative_path"] for item in event.payload["changes"]] == [
            item["relative_path"] for item in changes
        ]

    asyncio.run(scenario())


def test_agent_runtime_manager_rejects_pending_file_change_when_review_context_cannot_be_persisted_complete(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _InteractiveFakeAdapter()},
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id="TASK-OVERSIZE-REVIEW",
            agent_session_id="AS-OVERSIZE-REVIEW",
            resident="Codex",
            provider="codex",
            working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-OVERSIZE-REVIEW"),
            run_state="running",
            started_at=now,
            updated_at=now,
        )
        manager.store.create(snapshot)
        manager._snapshots[snapshot.agent_session_id] = snapshot

        before_events = manager.store.read_events(snapshot.agent_session_id)
        try:
            await manager._record_event(
                snapshot.agent_session_id,
                "file_change",
                {
                    "operation_id": "oversize-review",
                    "phase": "proposed",
                    "status": "pending_approval",
                    "changes": [{
                        "path": "D:/workspace/result.txt",
                        "relative_path": "result.txt",
                        "change_type": "modify",
                        "diff": "x" * 40_000,
                    }],
                },
            )
        except AgentRuntimeManagerError as exc:
            assert "review context" in str(exc)
        else:
            raise AssertionError("oversized pending file-change review context was accepted")

        after_events = manager.store.read_events(snapshot.agent_session_id)
        assert after_events == before_events
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "running"

    asyncio.run(scenario())


def test_agent_session_store_recovers_last_event_seq_from_durable_log(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    original = AgentSessionSnapshot(
        task_id="TASK-CRASH-SEQ",
        agent_session_id="AS-CRASH-SEQ",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-CRASH-SEQ"),
        run_state="running",
        started_at=now,
        updated_at=now,
    )
    store.create(original)
    first, _ = store.append_event(original, "run_state", {"state": "running"})

    # Simulate a crash after events.jsonl fsync but before session.json caught up.
    store.save_snapshot(original)
    recovered = store.load_snapshot(original.agent_session_id)
    second, recovered = store.append_event(recovered, "error", {"message": "recovered"})

    assert recovered.last_event_seq == 2
    assert [event["seq"] for event in store.read_events(original.agent_session_id)] == [1, 2]
    assert first.to_protocol()["event_id"] != second.to_protocol()["event_id"]


def test_agent_session_store_discards_only_incomplete_jsonl_tail(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    snapshot = AgentSessionSnapshot(
        task_id="TASK-CRASH-TAIL",
        agent_session_id="AS-CRASH-TAIL",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-CRASH-TAIL"),
        run_state="running",
        started_at=now,
        updated_at=now,
    )
    store.create(snapshot)
    store.append_event(snapshot, "run_state", {"state": "running"})
    event_path = tmp_path / "runtime" / "agent_sessions" / snapshot.agent_session_id / "events.jsonl"
    valid_bytes = event_path.read_bytes()
    with event_path.open("ab") as handle:
        handle.write(b'{"seq":2,"partial"')

    events = store.read_events(snapshot.agent_session_id)

    assert len(events) == 1
    assert events[0]["seq"] == 1
    assert event_path.read_bytes() == valid_bytes


def test_agent_runtime_manager_recovers_interrupted_session_only_after_explicit_resume(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVER"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("original recovery task\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(AgentSessionSnapshot(
            task_id="TASK-RECOVER",
            agent_session_id="AS-RECOVER-OLD",
            resident="Codex",
            provider="codex",
            working_dir=str(workspace),
            run_state="running",
            started_at=now,
            updated_at=now,
            provider_session_id="thread-existing",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ))
        adapter = _RecoveryCaptureAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )
        interrupted = manager.snapshot_payload("AS-RECOVER-OLD")
        assert interrupted["session"]["run_state"] == "interrupted"
        assert interrupted["recovery_options"] == ["resume", "rerun", "abandon"]
        assert adapter.requests == []

        resumed = await manager.recover_session("AS-RECOVER-OLD", "resume")
        source_after_choice = manager.snapshot_payload("AS-RECOVER-OLD")
        assert source_after_choice["session"]["run_state"] == "cancelled"
        assert source_after_choice["recovery_options"] == []
        assert source_after_choice["session"]["recovered_by_agent_session_id"] == resumed.agent_session_id
        with pytest.raises(AgentRuntimeManagerError, match="already consumed"):
            await manager.recover_session("AS-RECOVER-OLD", "resume")

        completed = await _wait_for_state(manager, resumed.agent_session_id, "completed")

        assert completed["session"]["final_summary"] == "recovered"
        assert completed["session"]["recovery_source_agent_session_id"] == "AS-RECOVER-OLD"
        assert completed["session"]["recovery_action"] == "resume"
        assert adapter.requests[-1].provider_session_id == "thread-existing"
        assert adapter.requests[-1].model == "gpt-5.6-sol"
        assert adapter.requests[-1].reasoning_effort == "xhigh"
        assert completed["session"]["model"] == "gpt-5.6-sol"
        assert completed["session"]["reasoning_effort"] == "xhigh"
        assert adapter.requests[-1].prompt.startswith("Resume the interrupted Nirai task")
        assert (workspace / "task.md").read_text(encoding="utf-8") == "original recovery task\n"

    asyncio.run(scenario())


def test_agent_runtime_manager_accepts_only_one_concurrent_recovery_choice(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVERY-RACE"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("race recovery task\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(AgentSessionSnapshot(
            task_id="TASK-RECOVERY-RACE",
            agent_session_id="AS-RECOVERY-RACE",
            resident="Codex",
            provider="codex",
            working_dir=str(workspace),
            run_state="running",
            started_at=now,
            updated_at=now,
            model="cursor-grok-4.6-xhigh",
            reasoning_effort=None,
        ))
        adapter = _RecoveryCaptureAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
        )

        async def recover_once():
            try:
                return await manager.recover_session("AS-RECOVERY-RACE", "rerun")
            except AgentRuntimeManagerError as exc:
                return exc

        first, second = await asyncio.gather(recover_once(), recover_once())
        outcomes = (first, second)
        recovered = [item for item in outcomes if isinstance(item, AgentSessionSnapshot)]
        rejected = [item for item in outcomes if isinstance(item, AgentRuntimeManagerError)]
        assert len(recovered) == 1
        assert len(rejected) == 1
        assert "already consumed" in str(rejected[0])
        assert manager.recovery_options("AS-RECOVERY-RACE") == []
        completed = await _wait_for_state(manager, recovered[0].agent_session_id, "completed")
        assert completed["session"]["final_summary"] == "recovered"
        assert completed["session"]["model"] == "cursor-grok-4.6-xhigh"
        assert adapter.requests[0].model == "cursor-grok-4.6-xhigh"
        assert len(adapter.requests) == 1

    asyncio.run(scenario())


def test_agent_runtime_manager_consumes_recovery_when_child_materializes_but_start_is_stopped(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVERY-START-FAIL"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("recovery start failure\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(AgentSessionSnapshot(
            task_id="TASK-RECOVERY-START-FAIL",
            agent_session_id="AS-RECOVERY-START-FAIL",
            resident="Codex",
            provider="codex",
            working_dir=str(workspace),
            run_state="running",
            started_at=now,
            updated_at=now,
        ))

        manager_ref: dict[str, AgentRuntimeManager] = {}

        async def stop_on_child_start(event) -> None:
            if event.type == "run_state" and event.payload.get("state") == "starting":
                await manager_ref["manager"].begin_stop()

        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _RecoveryCaptureAdapter()},
            broadcast=stop_on_child_start,
        )
        manager_ref["manager"] = manager

        with pytest.raises(AgentRuntimeManagerError, match="stopping"):
            await manager.recover_session("AS-RECOVERY-START-FAIL", "rerun")

        source = manager.snapshot_payload("AS-RECOVERY-START-FAIL")
        child_id = source["session"]["recovered_by_agent_session_id"]
        assert isinstance(child_id, str) and child_id
        child = manager.snapshot_payload(child_id)
        assert source["session"]["run_state"] == "cancelled"
        assert source["recovery_options"] == []
        assert child["session"]["run_state"] == "cancelled"

    asyncio.run(scenario())


def test_agent_runtime_manager_repairs_one_shot_recovery_links_after_restart(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVERY-LINK"
    workspace.mkdir(parents=True)
    (workspace / "task.md").write_text("task\n", encoding="utf-8")
    store.create(AgentSessionSnapshot(
        task_id="TASK-RECOVERY-LINK",
        agent_session_id="AS-RECOVERY-SOURCE",
        resident="Codex",
        provider="codex",
        working_dir=str(workspace),
        run_state="interrupted",
        started_at=now,
        updated_at=now,
        recovered_by_agent_session_id="AS-RECOVERY-CHILD",
    ))
    store.create(AgentSessionSnapshot(
        task_id="TASK-RECOVERY-LINK",
        agent_session_id="AS-RECOVERY-CHILD",
        resident="Codex",
        provider="codex",
        working_dir=str(workspace),
        run_state="starting",
        started_at=now,
        updated_at=now,
        recovery_source_agent_session_id="AS-RECOVERY-SOURCE",
        recovery_action="rerun",
    ))

    manager = AgentRuntimeManager(
        tmp_path,
        ("runtime\\workspace",),
        adapters={"codex": _RecoveryCaptureAdapter()},
    )

    source = manager.snapshot_payload("AS-RECOVERY-SOURCE")
    child = manager.snapshot_payload("AS-RECOVERY-CHILD")
    assert source["session"]["run_state"] == "cancelled"
    assert source["recovery_options"] == []
    assert child["session"]["run_state"] == "interrupted"


def test_agent_runtime_manager_reopens_recovery_when_reserved_child_was_never_created(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVERY-ORPHAN"
    workspace.mkdir(parents=True)
    (workspace / "task.md").write_text("task\n", encoding="utf-8")
    store.create(AgentSessionSnapshot(
        task_id="TASK-RECOVERY-ORPHAN",
        agent_session_id="AS-RECOVERY-ORPHAN",
        resident="Codex",
        provider="codex",
        working_dir=str(workspace),
        run_state="interrupted",
        started_at=now,
        updated_at=now,
        recovered_by_agent_session_id="AS-NOT-CREATED",
    ))

    manager = AgentRuntimeManager(
        tmp_path,
        ("runtime\\workspace",),
        adapters={"codex": _RecoveryCaptureAdapter()},
    )

    repaired = manager.snapshot_payload("AS-RECOVERY-ORPHAN")
    assert repaired["session"]["run_state"] == "interrupted"
    assert repaired["session"]["recovered_by_agent_session_id"] is None
    assert repaired["recovery_options"] == ["rerun", "abandon"]


def test_agent_runtime_manager_hides_resume_when_adapter_lacks_crash_resume_capability(tmp_path: Path) -> None:
    class NoCrashResumeAdapter(_RecoveryCaptureAdapter):
        capabilities = frozenset()

    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-NO-RESUME"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("task\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(AgentSessionSnapshot(
            task_id="TASK-NO-RESUME",
            agent_session_id="AS-NO-RESUME",
            resident="Codex",
            provider="codex",
            working_dir=str(workspace),
            run_state="running",
            started_at=now,
            updated_at=now,
            provider_session_id="thread-not-crash-safe",
        ))
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": NoCrashResumeAdapter()},
        )

        assert manager.recovery_options("AS-NO-RESUME") == ["rerun", "abandon"]
        with pytest.raises(AgentRuntimeManagerError, match="crash-safe"):
            await manager.recover_session("AS-NO-RESUME", "resume")

    asyncio.run(scenario())


def test_agent_runtime_manager_can_explicitly_abandon_interrupted_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-ABANDON"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("task\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(AgentSessionSnapshot(
            task_id="TASK-ABANDON",
            agent_session_id="AS-ABANDON",
            resident="Codex",
            provider="codex",
            working_dir=str(workspace),
            run_state="running",
            started_at=now,
            updated_at=now,
        ))
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _RecoveryCaptureAdapter()},
        )

        abandoned = await manager.recover_session("AS-ABANDON", "abandon")

        assert abandoned.run_state == "cancelled"
        assert abandoned.task_phase == "cancelled"
        assert "abandoned" in (abandoned.final_summary or "")
        assert manager.recovery_options("AS-ABANDON") == []

    asyncio.run(scenario())


def test_agent_runtime_manager_marks_nonterminal_sessions_interrupted_on_restart(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    store.create(AgentSessionSnapshot(
        task_id="TASK-OLD",
        agent_session_id="AS-OLD",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-OLD"),
        run_state="waiting_for_master",
        started_at=now,
        updated_at=now,
        pending_request_id="approve-old",
        pending_request_kind="approval",
    ))

    manager = AgentRuntimeManager(
        tmp_path,
        ("runtime\\workspace",),
        adapters={"codex": _InteractiveFakeAdapter()},
    )
    payload = manager.snapshot_payload("AS-OLD")

    assert payload["session"]["run_state"] == "interrupted"
    assert payload["session"]["pending_request_id"] is None
    assert payload["session"]["pending_request_kind"] is None
    assert payload["events"][-1]["payload"]["state"] == "interrupted"
