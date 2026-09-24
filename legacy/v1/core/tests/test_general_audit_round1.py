from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from core.agents import (
    AgentResourceBusyError,
    AgentRuntimeManager,
    AgentSessionSnapshot,
    AgentSessionStore,
)
from core.agents.types import utc_now_iso
from core.conversation import ConversationRuntimeError, ConversationStore
from core.incidents import IncidentStore
from core.server import CoreServer


async def _wait_for_state(manager: AgentRuntimeManager, agent_session_id: str, state: str) -> dict[str, Any]:
    for _ in range(200):
        payload = manager.snapshot_payload(agent_session_id)
        if payload["session"]["run_state"] == state:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Agent Session did not reach {state}; last="
        f"{manager.snapshot_payload(agent_session_id)['session']['run_state']}"
    )


class _IdleAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running", "provider_session_id": "thread-probe"})
        await self.release.wait()
        return "done"

    async def cancel(self, agent_session_id: str) -> bool:
        self.release.set()
        return True


class _EmitCancelledThenHoldAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.hold = asyncio.Event()
        self.in_hold = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        await emit("run_state", {"state": "cancelled"})
        self.in_hold.set()
        await self.hold.wait()
        return None

    async def cancel(self, agent_session_id: str) -> bool:
        return True


class _HangingInterruptLateApprovalAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.mid = asyncio.Event()
        self.cancel_started = asyncio.Event()
        self.wrote = False

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        self.mid.set()
        await self.cancel_started.wait()
        await emit(
            "approval_request",
            {
                "request_id": "late-after-cancel",
                "kind": "file_change",
                "title": "Late write after cancel began",
            },
        )
        try:
            answer = await wait_for_master("late-after-cancel", "approval", {})
        except Exception:
            return None
        if answer.get("decision") == "approve_once":
            (request.working_dir / "LATE.txt").write_text("late-write", encoding="utf-8")
            self.wrote = True
        return "applied"

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancel_started.set()
        await asyncio.Event().wait()
        return True


class _HangingInterruptResurrectAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.mid = asyncio.Event()
        self.cancel_started = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        self.mid.set()
        await self.cancel_started.wait()
        await emit("run_state", {"state": "running"})
        await asyncio.Event().wait()
        return None

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancel_started.set()
        await asyncio.Event().wait()
        return True


def test_start_session_cancellederror_converges_starting_orphan(tmp_path: Path) -> None:
    async def scenario() -> None:
        hold_starting = asyncio.Event()
        saw_starting = asyncio.Event()

        async def broadcast(event) -> None:
            if event.type == "run_state" and event.payload.get("state") == "starting":
                saw_starting.set()
                await hold_starting.wait()

        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": _IdleAdapter()},
            broadcast=broadcast,
        )
        start_task = asyncio.create_task(
            manager.start_session(
                task_id="TASK-START-CANCEL",
                resident="Codex",
                provider="codex",
                prompt="start then cancel the caller",
                working_dir=str(project),
            )
        )
        await asyncio.wait_for(saw_starting.wait(), timeout=1.0)
        start_task.cancel()
        hold_starting.set()
        with pytest.raises(asyncio.CancelledError):
            await start_task

        snapshots = manager.list_snapshots()
        assert len(snapshots) == 1
        assert snapshots[0].run_state == "cancelled"
        assert snapshots[0].agent_session_id not in manager._tasks
        working = Path(snapshots[0].working_dir)
        assert manager.resource_available(working) is True

        second = await manager.start_session(
            task_id="TASK-START-CANCEL-2",
            resident="Codex",
            provider="codex",
            prompt="same workspace after orphan was converged",
            working_dir=str(project),
        )
        assert second.run_state in {"starting", "running"}

    asyncio.run(scenario())


def test_provider_cancelled_event_keeps_workspace_locked_until_adapter_exits(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _EmitCancelledThenHoldAdapter()
        project = tmp_path / "projects" / "ProjectA"
        project.mkdir(parents=True)
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
            max_concurrent_sessions=4,
        )
        first = await manager.start_session(
            task_id="TASK-EARLY-TERMINAL",
            resident="Codex",
            provider="codex",
            prompt="emit cancelled then hold",
            working_dir=str(project),
        )
        await asyncio.wait_for(adapter.in_hold.wait(), timeout=1.0)
        payload = manager.snapshot_payload(first.agent_session_id)
        working = Path(payload["session"]["working_dir"])

        assert payload["session"]["run_state"] != "cancelled"
        assert manager.resource_available(working) is False
        assert first.agent_session_id in manager._tasks
        assert manager._tasks[first.agent_session_id].done() is False

        with pytest.raises(AgentResourceBusyError):
            await manager.start_session(
                task_id="TASK-EARLY-TERMINAL-2",
                resident="Codex",
                provider="codex",
                prompt="must not share the live workspace",
                working_dir=str(project),
            )

        adapter.hold.set()
        await _wait_for_state(manager, first.agent_session_id, "completed")
        assert manager.resource_available(working) is True

    asyncio.run(scenario())


def test_late_approval_after_cancelling_does_not_reopen_master_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _HangingInterruptLateApprovalAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            interrupt_timeout_sec=2.0,
        )
        snapshot = await manager.start_session(
            task_id="TASK-LATE-APPROVAL",
            resident="Codex",
            provider="codex",
            prompt="cancel then late approval",
        )
        await asyncio.wait_for(adapter.mid.wait(), timeout=1.0)

        cancel_task = asyncio.create_task(manager.cancel(snapshot.agent_session_id))
        await asyncio.wait_for(adapter.cancel_started.wait(), timeout=1.0)
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "cancelling"

        for _ in range(50):
            state = manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"]
            if state == "waiting_for_master":
                break
            await asyncio.sleep(0.01)
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] != "waiting_for_master"

        accepted = await manager.respond(
            snapshot.agent_session_id,
            "late-after-cancel",
            "approval",
            {"decision": "approve_once"},
        )
        assert accepted is False
        await asyncio.sleep(0.05)
        assert adapter.wrote is False
        assert not (Path(snapshot.working_dir) / "LATE.txt").exists()

        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task

    asyncio.run(scenario())


def test_run_state_event_does_not_resurrect_cancelling_to_running(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _HangingInterruptResurrectAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": adapter},
            interrupt_timeout_sec=2.0,
        )
        snapshot = await manager.start_session(
            task_id="TASK-RESURRECT",
            resident="Codex",
            provider="codex",
            prompt="cancelling then running",
        )
        await asyncio.wait_for(adapter.mid.wait(), timeout=1.0)
        cancel_task = asyncio.create_task(manager.cancel(snapshot.agent_session_id))
        await asyncio.wait_for(adapter.cancel_started.wait(), timeout=1.0)
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "cancelling"

        await asyncio.sleep(0.05)
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "cancelling"

        cancel_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancel_task

    asyncio.run(scenario())


def test_finish_session_keeps_first_terminal_state(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": _IdleAdapter()},
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id="TASK-TERMINAL-OVERWRITE",
            agent_session_id="AS-TERMINAL-OVERWRITE",
            resident="Codex",
            provider="codex",
            working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-TERMINAL-OVERWRITE"),
            run_state="running",
            started_at=now,
            updated_at=now,
        )
        manager.store.create(snapshot)
        manager._snapshots[snapshot.agent_session_id] = snapshot

        await manager._finish_session(snapshot.agent_session_id, "completed", "done")
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "completed"
        await manager._finish_session(snapshot.agent_session_id, "cancelled", None)
        assert manager.snapshot_payload(snapshot.agent_session_id)["session"]["run_state"] == "completed"
        states = [
            event["payload"].get("state")
            for event in manager.snapshot_payload(snapshot.agent_session_id)["events"]
            if event["type"] == "run_state"
        ]
        assert states[-1] == "completed"
        assert "cancelled" not in states

    asyncio.run(scenario())


def test_recover_cancellederror_restores_source_and_converges_child(tmp_path: Path) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "runtime" / "workspace" / "TASK-RECOVER-CANCEL"
        workspace.mkdir(parents=True)
        (workspace / "task.md").write_text("original recovery task\n", encoding="utf-8")
        store = AgentSessionStore(tmp_path)
        now = utc_now_iso()
        store.create(
            AgentSessionSnapshot(
                task_id="TASK-RECOVER-CANCEL",
                agent_session_id="AS-RECOVER-SRC",
                resident="Codex",
                provider="codex",
                working_dir=str(workspace),
                run_state="running",
                started_at=now,
                updated_at=now,
                provider_session_id="thread-old",
            )
        )

        hold_starting = asyncio.Event()
        saw_starting = asyncio.Event()

        async def broadcast(event) -> None:
            if event.type == "run_state" and event.payload.get("state") == "starting":
                saw_starting.set()
                await hold_starting.wait()

        class ResumeAdapter:
            provider = "codex"
            capabilities = frozenset({"crash_resume"})

            async def run(self, request, *, emit, wait_for_master):
                await emit("run_state", {"state": "running"})
                await asyncio.Event().wait()
                return None

            async def cancel(self, agent_session_id: str) -> bool:
                return True

        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace",),
            adapters={"codex": ResumeAdapter()},
            broadcast=broadcast,
        )
        recover_task = asyncio.create_task(manager.recover_session("AS-RECOVER-SRC", "rerun"))
        await asyncio.wait_for(saw_starting.wait(), timeout=1.0)
        recover_task.cancel()
        hold_starting.set()
        with pytest.raises(asyncio.CancelledError):
            await recover_task

        source = manager.snapshot_payload("AS-RECOVER-SRC")["session"]
        children = [item for item in manager.list_snapshots() if item.agent_session_id != "AS-RECOVER-SRC"]
        assert source["recovered_by_agent_session_id"] is None
        assert source["run_state"] == "interrupted"
        assert manager.recovery_options("AS-RECOVER-SRC") == ["resume", "rerun", "abandon"]
        assert len(children) == 1
        assert children[0].run_state == "cancelled"
        assert children[0].agent_session_id not in manager._tasks

    asyncio.run(scenario())


def test_conversation_store_rejects_stale_finish_after_cancel(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    running = store.start_turn(conversation, holo_sender="Holo", text="止めて")
    cancelled = store.end_turn(running, "cancelled", error="user cancel")
    assert cancelled.turn_state == "cancelled"
    with pytest.raises(ConversationRuntimeError, match="no longer running"):
        store.finish_turn(running, sender="Serina", text="should not land after cancel")
    latest = store.load(conversation.conversation_id)
    assert latest.turn_state == "cancelled"
    assert all(message.text != "should not land after cancel" for message in latest.messages)


def test_conversation_journal_does_not_concatenate_truncated_tail(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
    )
    conversation = store.append_message(
        conversation,
        role="holo",
        sender="Holo",
        text="complete-line",
    )
    journal = store._journal_path(conversation.conversation_id)
    raw = journal.read_bytes()
    journal.write_bytes(raw[:-5])
    conversation = store.append_message(
        conversation,
        role="participant",
        sender="cursor",
        text="next-message",
    )
    lines = [line for line in journal.read_bytes().split(b"\n") if line.strip()]
    decoded = []
    for line in lines:
        try:
            decoded.append(__import__("json").loads(line.decode("utf-8")))
        except (UnicodeDecodeError, ValueError):
            continue
    texts = [item.get("text") for item in decoded]
    assert "next-message" in texts
    full = store.full_messages(store.load(conversation.conversation_id))
    assert [message.text for message in full][-1] == "next-message"


def test_truncated_episode_utf8_does_not_block_core_start(tmp_path: Path) -> None:
    from core.tests.test_server import _make_config

    config = _make_config(tmp_path)
    server = CoreServer(config, port_override=0)
    session_id = server.sessions.active_session_id
    entry = server.sessions.append_master_say("珊瑚の洞窟を覚えている", "episode-utf8")
    server.world_memory.record_public_entry(entry)
    path = server.world_memory._episode_path(session_id)
    data = path.read_bytes()
    cut = data.rfind("珊".encode("utf-8"))
    assert cut != -1
    path.write_bytes(data[: cut + 1])

    restored = CoreServer(config, port_override=0)
    raw = restored.world_memory.raw_entries_for_session(session_id)
    assert any(item.get("text") == "珊瑚の洞窟を覚えている" for item in raw)
    episode = restored.world_memory._legacy_episode_text(path)
    assert episode is not None
    assert "珊瑚の洞窟を覚えている" in episode


def test_incident_fallback_invalid_utf8_is_quarantined_and_valid_rows_replay(tmp_path: Path) -> None:
    from core.tests.test_server import _make_config

    store = IncidentStore(tmp_path)
    store.append_fallback(
        component="nirai.core.test",
        code="valid_before_garbage",
        severity="error",
        summary="keep this row",
    )
    with store.fallback_path.open("ab") as handle:
        handle.write(b"\xff\xfe")

    replayed = store.replay_fallback()
    assert replayed >= 2
    assert store.fallback_pending() is False
    rows = store.unresolved(limit=20)
    assert any(row["code"] == "valid_before_garbage" for row in rows)
    assert any(row["code"] == "incident_fallback_corrupt_record" for row in rows)

    server = CoreServer(_make_config(tmp_path), port_override=0)
    health = server._holo_health_snapshot()
    assert health["status"] in {"ok", "attention"}
    assert isinstance(health["unresolved_incident_count"], int)
