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


def test_workspace_policy_resolves_named_allowed_root_and_rejects_unknown_or_ambiguous_target(tmp_path: Path) -> None:
    project_a = tmp_path / "projects" / "ProjectA"
    duplicate_a = tmp_path / "other" / "ProjectA"
    project_a.mkdir(parents=True)
    duplicate_a.mkdir(parents=True)

    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\ProjectA"),
    )
    assert policy.named_working_dir("projecta", task_id="TASK-NAMED") == project_a.resolve()
    with pytest.raises(AgentSafetyError, match="not in tasks.allowed_dirs"):
        policy.named_working_dir("workspace", task_id="TASK-NAMED")

    internal_child = tmp_path / "runtime" / "workspace" / "Shared"
    internal_child.mkdir(parents=True)
    internal_policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "runtime\\workspace\\Shared"),
    )
    with pytest.raises(AgentSafetyError, match="not in tasks.allowed_dirs"):
        internal_policy.named_working_dir("Shared", task_id="TASK-NAMED")

    with pytest.raises(AgentSafetyError, match="not in tasks.allowed_dirs"):
        policy.named_working_dir("Unknown", task_id="TASK-NAMED")
    with pytest.raises(AgentSafetyError, match="invalid"):
        policy.named_working_dir("../ProjectA", task_id="TASK-NAMED")

    japanese = tmp_path / "projects" / "日本語PJ"
    japanese.mkdir(parents=True)
    unicode_policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\日本語PJ"),
    )
    assert unicode_policy.named_working_dir("日本語PJ", task_id="TASK-NAMED") == japanese.resolve()

    ambiguous = AgentWorkspacePolicy(
        tmp_path,
        ("projects\\ProjectA", "other\\ProjectA"),
    )
    with pytest.raises(AgentSafetyError, match="ambiguous"):
        ambiguous.named_working_dir("ProjectA", task_id="TASK-NAMED")


def test_workspace_policy_named_target_requires_existing_external_root(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\MissingProject"),
    )

    with pytest.raises(AgentSafetyError, match="does not exist"):
        policy.named_working_dir("MissingProject", task_id="TASK-MISSING")
    assert (tmp_path / "projects" / "MissingProject").exists() is False


def test_workspace_policy_reserves_all_nirai_runtime_state_from_named_and_direct_agent_cwd(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    for relative in ("runtime\\agent_sessions", "runtime\\chat_sessions"):
        (tmp_path / Path(relative)).mkdir(parents=True, exist_ok=True)
    policy = AgentWorkspacePolicy(
        tmp_path,
        (
            "runtime",
            "runtime\\workspace",
            "runtime\\agent_sessions",
            "runtime\\chat_sessions",
            "projects\\ProjectA",
        ),
    )

    for target_name in ("runtime", "workspace", "agent_sessions", "chat_sessions"):
        with pytest.raises(AgentSafetyError, match="not in tasks.allowed_dirs"):
            policy.named_working_dir(target_name, task_id="TASK-RUNTIME")

    for requested in (
        "runtime",
        "runtime\\agent_sessions",
        "runtime\\chat_sessions",
        "runtime\\workspace\\OTHER-TASK",
    ):
        with pytest.raises(AgentSafetyError, match="internal runtime state"):
            policy.resolve_working_dir(requested, task_id="TASK-RUNTIME")

    own_workspace = policy.resolve_working_dir(None, task_id="TASK-RUNTIME")
    assert own_workspace == (tmp_path / "runtime" / "workspace" / "TASK-RUNTIME").resolve()
    assert policy.named_working_dir("ProjectA", task_id="TASK-RUNTIME") == project.resolve()


def test_workspace_policy_default_task_workspace_is_order_independent(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("projects\\ProjectA", "runtime\\workspace"),
    )

    expected = (tmp_path / "runtime" / "workspace" / "TASK-ORDER").resolve()
    assert policy.resolve_working_dir(None, task_id="TASK-ORDER") == expected
    assert policy.task_metadata_dir("TASK-ORDER") == expected


def test_workspace_policy_wraps_metadata_directory_creation_failure(tmp_path: Path) -> None:
    (tmp_path / "runtime").write_text("not a directory", encoding="utf-8")
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))

    with pytest.raises(AgentSafetyError, match="metadata directory could not be prepared"):
        policy.task_metadata_dir("TASK-MKDIR")


def test_workspace_policy_rejects_provider_write_escape(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
    working_dir = policy.resolve_working_dir(None, task_id="TASK-1")

    assert policy.assert_write_path(Path("nested/file.txt"), working_dir=working_dir) == (
        working_dir / "nested" / "file.txt"
    ).resolve()
    with pytest.raises(AgentSafetyError, match="escaped"):
        policy.assert_write_path(Path("..\\other.txt"), working_dir=working_dir)


def test_workspace_policy_prepares_only_descendants_without_recreating_deleted_workspace_root(tmp_path: Path) -> None:
    project = tmp_path / "projects" / "ProjectA"
    project.mkdir(parents=True)
    policy = AgentWorkspacePolicy(
        tmp_path,
        ("runtime\\workspace", "projects\\ProjectA"),
    )

    nested = policy.prepare_write_path(
        Path("nested/deeper/result.txt"),
        working_dir=project,
    )
    assert nested == (project / "nested" / "deeper" / "result.txt").resolve()
    assert nested.parent.is_dir()

    import shutil
    shutil.rmtree(project)
    with pytest.raises(AgentSafetyError, match="working directory does not exist"):
        policy.prepare_write_path(Path("result.txt"), working_dir=project)
    assert project.exists() is False


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
