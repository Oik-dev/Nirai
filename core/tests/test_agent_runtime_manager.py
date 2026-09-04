from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.agents import (
    AgentRunRequest,
    AgentRuntimeError,
    AgentRuntimeManager,
    AgentRuntimeManagerError,
    AgentSessionSnapshot,
    AgentSessionStore,
)
from core.agents.types import utc_now_iso


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


def test_agent_runtime_manager_rejects_second_session_while_first_is_starting_or_running(tmp_path: Path) -> None:
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

        try:
            await manager.start_session(
                task_id="TASK-SECOND",
                resident="Codex",
                provider="codex",
                prompt="second",
            )
        except Exception as exc:
            assert "already running" in str(exc)
        else:
            raise AssertionError("second concurrent Agent Session was accepted")

        assert first.agent_session_id in {snapshot.agent_session_id for snapshot in manager.list_snapshots()}
        adapter.release.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")

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

        await manager._finish_session(snapshot.agent_session_id, "completed", "z" * 20_000)
        finished = manager.snapshot_payload(snapshot.agent_session_id)["session"]
        assert len(finished["final_summary"]) <= 8_000
        assert finished["final_summary"].endswith("…")

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
