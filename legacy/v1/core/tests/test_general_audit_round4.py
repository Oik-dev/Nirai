from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from core.agents import (
    AgentResourceBusyError,
    AgentRuntimeManager,
    AgentSessionSnapshot,
    AgentSessionStore,
)
from core.agents.types import utc_now_iso
from core.server import CoreServer
from core.tests.test_server import _make_config


class _IdleAdapter:
    provider = "codex"

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running", "provider_session_id": "thread-round4"})
        await self.release.wait()
        return "done"

    async def cancel(self, agent_session_id: str) -> bool:
        self.release.set()
        return True


async def _wait_for_state(manager: AgentRuntimeManager, agent_session_id: str, state: str) -> dict:
    for _ in range(200):
        payload = manager.snapshot_payload(agent_session_id)
        if payload["session"]["run_state"] == state:
            return payload
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"Agent Session did not reach {state}; last="
        f"{manager.snapshot_payload(agent_session_id)['session']['run_state']}"
    )


def test_interrupted_named_workspace_stays_exclusive_while_concurrency_slot_is_free(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    store.create(AgentSessionSnapshot(
        task_id="TASK-INTERRUPTED",
        agent_session_id="AS-INTERRUPTED",
        resident="Codex",
        provider="codex",
        working_dir=str(project),
        run_state="running",
        started_at=now,
        updated_at=now,
        origin_chat_session_id="S-ORIGIN",
        task_phase="running",
    ))

    async def scenario() -> None:
        adapter = _IdleAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
            max_concurrent_sessions=1,
        )
        interrupted = manager.snapshot_payload("AS-INTERRUPTED")
        assert interrupted["session"]["run_state"] == "interrupted"
        assert manager.has_active_session() is False
        assert manager.resource_available(project) is False

        with pytest.raises(AgentResourceBusyError, match="same workspace"):
            await manager.start_session(
                task_id="TASK-OVERWRITE",
                resident="Codex",
                provider="codex",
                prompt="overwrite interrupted tree",
                working_dir=str(project),
            )

        other = await manager.start_session(
            task_id="TASK-OTHER",
            resident="Codex",
            provider="codex",
            prompt="independent workspace work",
        )
        await _wait_for_state(manager, other.agent_session_id, "running")
        adapter.release.set()
        await _wait_for_state(manager, other.agent_session_id, "completed")

    asyncio.run(scenario())


def test_core_restart_keeps_unrecovered_interrupt_as_active_owner(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bootstrap = CoreServer(config, port_override=0)
    origin_session_id = bootstrap.sessions.active_session_id
    now = utc_now_iso()
    bootstrap.agent_runtime.store.create(AgentSessionSnapshot(
        task_id="TASK-OWNER",
        agent_session_id="AS-OWNER",
        resident="Lapan",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-OWNER"),
        run_state="running",
        started_at=now,
        updated_at=now,
        origin_chat_session_id=origin_session_id,
        task_phase="running",
    ))

    recovered = CoreServer(config, port_override=0)
    snapshot = recovered.agent_runtime.snapshot_payload("AS-OWNER")["session"]
    assert snapshot["run_state"] == "interrupted"
    assert snapshot["task_phase"] == "interrupted"
    assert snapshot["result_reported"] is False
    assert recovered._resident_has_active_agent_work("Lapan") is True
    assert recovered._chat_session_has_active_agent_task(origin_session_id) is True
    assert recovered._recovered_agent_notifications == {}


def test_core_restart_reports_leftover_cursor_rollback_backup(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    leftover = tmp_path / "runtime" / "cursor_recovery" / ".RB-leftover"
    leftover.mkdir(parents=True)
    (leftover / "seed.txt").write_text("backup\n", encoding="utf-8")
    (leftover / "recovery.json").write_text(
        json.dumps({
            "version": 1,
            "state": "applying",
            "working_dir": str(tmp_path / "projects" / "ProjectA"),
            "changes": [{
                "relative_path": "seed.txt",
                "change_type": "modify",
                "backup_relative_path": "seed.txt",
            }],
        }),
        encoding="utf-8",
    )

    server = CoreServer(config, port_override=0)
    assert server.incidents is not None
    rows = [
        row
        for row in server.incidents.unresolved()
        if row["code"] == "cursor_rollback_orphan"
    ]
    assert len(rows) == 1
    assert "state=applying" in rows[0]["detail"]
    assert f"working_dir={tmp_path / 'projects' / 'ProjectA'}" in rows[0]["detail"]
    assert "changes=1" in rows[0]["detail"]
    assert leftover.is_dir()


def test_recovered_source_keeps_workspace_until_child_snapshot_exists(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    store = AgentSessionStore(tmp_path)
    now = utc_now_iso()
    store.create(AgentSessionSnapshot(
        task_id="TASK-INTERRUPTED",
        agent_session_id="AS-INTERRUPTED",
        resident="Codex",
        provider="codex",
        working_dir=str(project),
        run_state="running",
        started_at=now,
        updated_at=now,
        origin_chat_session_id="S-ORIGIN",
        task_phase="running",
    ))

    async def scenario() -> None:
        adapter = _IdleAdapter()
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime\\workspace", "projects\\ProjectA"),
            adapters={"codex": adapter},
            max_concurrent_sessions=2,
        )
        source = manager._snapshots["AS-INTERRUPTED"]
        reserved = source.with_updates(recovered_by_agent_session_id="AS-CHILD-MISSING")
        manager.store.save_snapshot(reserved)
        manager._snapshots["AS-INTERRUPTED"] = reserved
        assert manager.resource_available(project) is False

        with pytest.raises(AgentResourceBusyError, match="same workspace"):
            await manager.start_session(
                task_id="TASK-OVERWRITE",
                resident="Codex",
                provider="codex",
                prompt="steal recovered tree",
                working_dir=str(project),
            )

        child = await manager.start_session(
            task_id="TASK-INTERRUPTED",
            resident="Codex",
            provider="codex",
            prompt="resume recovered tree",
            working_dir=str(project),
            preallocated_agent_session_id="AS-CHILD-MISSING",
            recovery_source_agent_session_id="AS-INTERRUPTED",
            recovery_action="rerun",
        )
        await _wait_for_state(manager, child.agent_session_id, "running")
        assert manager.resource_available(project) is False
        adapter.release.set()
        await _wait_for_state(manager, child.agent_session_id, "completed")

    asyncio.run(scenario())
