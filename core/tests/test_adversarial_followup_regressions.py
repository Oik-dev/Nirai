from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from core.agents.base import AgentRunRequest, AgentRuntimeError, AgentRuntimeUnavailableError
from core.agents.codex_app_server import CodexAppServerAdapter
from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.safety import AgentWorkspacePolicy
from core.incidents import IncidentStore
from core.sessions.chat_store import ChatStore


async def _noop(*_args, **_kwargs):
    return None


def _request(policy: AgentWorkspacePolicy, provider: str) -> AgentRunRequest:
    return AgentRunRequest(
        task_id="T-FOLLOWUP",
        agent_session_id="AS-FOLLOWUP",
        resident="Followup",
        provider=provider,
        prompt="offline regression",
        working_dir=policy.resolve_working_dir(None, task_id="T-FOLLOWUP"),
    )


async def _approve(*_args, **_kwargs):
    return {"decision": "approve_once"}


def _setup_fake_cursor_cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
    adapter = CursorAcpAdapter(policy)
    request = _request(policy, "cursor")
    (request.working_dir / "a.txt").write_text("ORIGINAL", encoding="utf-8")
    home = tmp_path / "runtime" / "cursor_agent_homes" / request.agent_session_id

    def fake_home(*_args, **_kwargs):
        home.mkdir(parents=True)
        return home

    async def fake_run(*_args, **kwargs):
        (kwargs["cwd"] / "a.txt").write_text("CHANGED", encoding="utf-8")
        return SimpleNamespace(
            returncode=0,
            stdout='{"result":"done","session_id":"fake-session"}',
            stderr="",
        )

    monkeypatch.setattr(adapter, "_prepare_cursor_home", fake_home)
    monkeypatch.setattr(adapter, "_harden_cursor_cli_home", lambda *_args: None)
    monkeypatch.setattr(adapter, "_build_cursor_environment", lambda *_args: {})
    monkeypatch.setattr("core.agents.cursor_acp.resolve_cursor_command", lambda: ("NEVER-STARTED",))
    monkeypatch.setattr(adapter._cli_process_manager, "run", fake_run)
    return adapter, request, home


def test_cursor_full_turn_recovery_backup_survives_outer_home_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter, request, home = _setup_fake_cursor_cli(tmp_path, monkeypatch)

        def fail_apply_and_rollback(source: Path, target: Path) -> None:
            target.write_text("PARTIAL", encoding="utf-8")
            raise OSError("simulated apply and rollback failure")

        original_replace = __import__("os").replace

        def fail_recovery_publish(source: Path | str, target: Path | str) -> None:
            if Path(source).name.startswith(".RB-"):
                raise OSError("simulated recovery publish failure")
            original_replace(source, target)

        monkeypatch.setattr(adapter, "_atomic_copy_file", fail_apply_and_rollback)
        monkeypatch.setattr("core.agents.cursor_acp.os.replace", fail_recovery_publish)

        with pytest.raises(AgentRuntimeError, match="original backup remains at"):
            await adapter._run_exact_cli(request, emit=_noop, wait_for_master=_approve)

        assert not home.exists()
        rollback_dirs = list((tmp_path / "runtime" / "cursor_recovery").glob(".RB-*"))
        assert len(rollback_dirs) == 1
        assert (rollback_dirs[0] / "a.txt").read_text(encoding="utf-8") == "ORIGINAL"
        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "PARTIAL"

    asyncio.run(scenario())


def test_cursor_recovery_root_failure_happens_before_real_workspace_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter, request, _home = _setup_fake_cursor_cli(tmp_path, monkeypatch)
        original_mkdir = Path.mkdir

        def fail_recovery_mkdir(path: Path, *args, **kwargs):
            if path.name == "cursor_recovery":
                raise OSError("simulated recovery root failure")
            return original_mkdir(path, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", fail_recovery_mkdir)
        with pytest.raises(AgentRuntimeError, match="recovery storage could not be prepared"):
            await adapter._run_exact_cli(request, emit=_noop, wait_for_master=_approve)
        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "ORIGINAL"

    asyncio.run(scenario())


def test_cursor_cancel_waits_until_approved_write_is_stable_before_releasing_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        adapter, request, home = _setup_fake_cursor_cli(tmp_path, monkeypatch)
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def delayed_write(source: Path, target: Path) -> None:
            contents = source.read_bytes()
            entered.set()
            assert release.wait(5)
            target.write_bytes(contents)
            finished.set()

        monkeypatch.setattr(adapter, "_atomic_copy_file", delayed_write)
        task = asyncio.create_task(adapter._run_exact_cli(request, emit=_noop, wait_for_master=_approve))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert task.done() is False
        assert home.exists()
        assert request.agent_session_id in adapter._runtime_owned_snapshot()

        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert finished.is_set()
        assert (request.working_dir / "a.txt").read_text(encoding="utf-8") == "CHANGED"
        assert not home.exists()
        assert request.agent_session_id not in adapter._runtime_owned_snapshot()

    asyncio.run(scenario())


def test_deleted_unsynced_chat_keeps_replay_source_without_blocking_other_sessions(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "chats")
    deleted = store.create_session()["id"]
    deleted_entry = store.append_entry(deleted, kind="say", sender="master", text="pending before delete")
    store.delete_session(deleted)
    other = store.create_session()["id"]
    other_entry = store.append_entry(other, kind="say", sender="master", text="other pending")

    pending = store.pending_memory_sync()
    assert [item["entry_id"] for item in pending] == [deleted_entry["entry_id"], other_entry["entry_id"]]
    assert pending[0]["session_id"] == deleted
    assert pending[1]["session_id"] == other

    store.mark_memory_synced(deleted_entry["entry_id"])
    remaining = store.pending_memory_sync()
    assert [item["entry_id"] for item in remaining] == [other_entry["entry_id"]]
    with store._connect_entries() as connection:
        hidden = connection.execute(
            "SELECT COUNT(*) FROM chat_entries WHERE entry_id=?",
            (deleted_entry["entry_id"],),
        ).fetchone()
    assert hidden is not None and int(hidden[0]) == 0


def test_codex_cancel_during_prepare_waits_for_late_home_then_cleans_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
        adapter = CodexAppServerAdapter(policy)
        request = _request(policy, "codex")
        entered = threading.Event()
        release = threading.Event()
        home = tmp_path / "runtime" / "codex_agent_homes" / request.agent_session_id

        def slow_prepare(*_args, **_kwargs):
            entered.set()
            assert release.wait(5)
            home.mkdir(parents=True)
            (home / "auth.json").write_text("FAKE AUTH ONLY", encoding="utf-8")
            return home

        monkeypatch.setattr(adapter, "_resolve_command", lambda: ("never-started",))
        monkeypatch.setattr(adapter, "_prepare_isolated_codex_home", slow_prepare)
        task = asyncio.create_task(adapter.run(request, emit=_noop, wait_for_master=_noop))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert task.done() is False
        assert request.agent_session_id in adapter._runtime_owned_snapshot()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not home.exists()
        assert request.agent_session_id not in adapter._runtime_owned_snapshot()

    asyncio.run(scenario())


def test_codex_cancel_during_spawn_cleans_prepared_credentials_before_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
        adapter = CodexAppServerAdapter(policy)
        request = _request(policy, "codex")
        entered = asyncio.Event()
        home = tmp_path / "runtime" / "codex_agent_homes" / request.agent_session_id

        def prepare(*_args, **_kwargs):
            home.mkdir(parents=True)
            (home / "auth.json").write_text("FAKE AUTH ONLY", encoding="utf-8")
            return home

        async def spawn(*_args, **_kwargs):
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(adapter, "_resolve_command", lambda: ("never-started",))
        monkeypatch.setattr(adapter, "_prepare_isolated_codex_home", prepare)
        monkeypatch.setattr(adapter, "_spawn", spawn)
        task = asyncio.create_task(adapter.run(request, emit=_noop, wait_for_master=_noop))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert not home.exists()
        assert request.agent_session_id not in adapter._runtime_owned_snapshot()

    asyncio.run(scenario())


def test_cursor_cancel_during_prepare_waits_for_stage_cleanup_before_releasing_claims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
        adapter = CursorAcpAdapter(policy)
        request = _request(policy, "cursor")
        entered = threading.Event()
        release = threading.Event()
        original = adapter._prepare_staging_workspace
        stage_result: list[Path] = []

        def slow_prepare(*args, **kwargs):
            entered.set()
            assert release.wait(5)
            stage, baseline = original(*args, **kwargs)
            stage_result.append(stage)
            return stage, baseline

        monkeypatch.setattr(adapter, "_prepare_staging_workspace", slow_prepare)
        task = asyncio.create_task(adapter.run(request, emit=_noop, wait_for_master=_noop))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0.05)
        assert task.done() is False
        assert request.agent_session_id in adapter._runtime_owned_snapshot()
        release.set()
        result = await asyncio.gather(task, return_exceptions=True)
        assert isinstance(result[0], asyncio.CancelledError)
        assert stage_result and not stage_result[0].exists()
        assert request.agent_session_id not in adapter._runtime_owned_snapshot()
        assert request.agent_session_id not in adapter._preparing_ids

    asyncio.run(scenario())


def test_incident_truncated_fallback_tail_is_quarantined_and_later_error_replays(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    store.fallback_path.write_bytes(b'{"component":"crash-truncated')
    store.append_fallback(
        component="probe",
        code="later_valid_error",
        severity="error",
        summary="valid error",
    )

    replayed = store.replay_fallback(limit=32)
    assert replayed >= 2  # corruption marker + the later valid error
    assert store.fallback_pending() is False
    rows = store.unresolved(limit=20)
    assert any(row["code"] == "incident_fallback_corrupt_record" for row in rows)
    assert any(row["code"] == "later_valid_error" for row in rows)
    assert store.fallback_quarantine_path.is_file()
    assert "truncated_tail_before_append" in store.fallback_quarantine_path.read_text(encoding="utf-8")
