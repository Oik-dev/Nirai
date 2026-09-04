from pathlib import Path

import pytest

from core.agents import (
    AgentSafetyError,
    AgentSessionSnapshot,
    AgentSessionStore,
    AgentWorkspacePolicy,
)
from core.agents.types import utc_now_iso


def _snapshot(tmp_path: Path) -> AgentSessionSnapshot:
    now = utc_now_iso()
    return AgentSessionSnapshot(
        task_id="TASK-1",
        agent_session_id="AGENT-1",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-1"),
        run_state="starting",
        started_at=now,
        updated_at=now,
    )


def test_workspace_policy_defaults_to_task_subdirectory_inside_allowed_root(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))

    working_dir = policy.resolve_working_dir(None, task_id="TASK-42")

    assert working_dir == (tmp_path / "runtime" / "workspace" / "TASK-42").resolve()
    assert working_dir.is_dir()


def test_workspace_policy_rejects_outside_and_protected_nirai_sources(tmp_path: Path) -> None:
    (tmp_path / "core").mkdir()
    (tmp_path / "world").mkdir()
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace", str(tmp_path)))

    with pytest.raises(AgentSafetyError, match="outside tasks.allowed_dirs"):
        AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",)).resolve_working_dir(
            str(tmp_path / "elsewhere"),
            task_id="TASK-1",
        )
    with pytest.raises(AgentSafetyError, match="M5"):
        policy.resolve_working_dir(str(tmp_path), task_id="TASK-2")
    with pytest.raises(AgentSafetyError, match="M5"):
        policy.resolve_working_dir(str(tmp_path / "core"), task_id="TASK-3")


def test_workspace_policy_rejects_provider_write_escape(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    working_dir = policy.resolve_working_dir(None, task_id="TASK-1")

    assert policy.assert_write_path(Path("nested/file.txt"), working_dir=working_dir) == (
        working_dir / "nested" / "file.txt"
    ).resolve()
    with pytest.raises(AgentSafetyError, match="escaped"):
        policy.assert_write_path(Path("..\\other.txt"), working_dir=working_dir)


def test_agent_session_store_persists_snapshot_and_ordered_events(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    snapshot = store.create(_snapshot(tmp_path))

    event1, snapshot = store.append_event(snapshot, "run_state", {"state": "running"})
    snapshot = snapshot.with_updates(run_state="running")
    store.save_snapshot(snapshot)
    event2, snapshot = store.append_event(snapshot, "command_execution", {"command": "npm test"})

    loaded = store.load_snapshot("AGENT-1")
    events = store.read_events("AGENT-1")

    assert event1.seq == 1
    assert event2.seq == 2
    assert loaded.run_state == "running"
    assert loaded.last_event_seq == 2
    assert [event["seq"] for event in events] == [1, 2]
    assert events[1]["payload"]["command"] == "npm test"


def test_agent_session_store_recovers_snapshots_after_restart(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    store.create(_snapshot(tmp_path))

    recovered = AgentSessionStore(tmp_path).list_snapshots()

    assert len(recovered) == 1
    assert recovered[0].agent_session_id == "AGENT-1"
    assert recovered[0].provider == "codex"
