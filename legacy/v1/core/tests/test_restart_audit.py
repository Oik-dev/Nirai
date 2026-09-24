from __future__ import annotations

import asyncio
import errno
import json
import logging
import os
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.agents.base import AgentRuntimeUnavailableError
from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.cursor_workspace import CursorWorkspaceMixin, _staging_os_error_detail
from core.agents.safety import AgentSafetyError, AgentWorkspacePolicy
from core.incidents import IncidentLogHandler, IncidentStore
from core.server import CoreServer
from core.protocol import make_message, parse_message, world_hello_payload
from websockets.asyncio.client import connect
from core.tests.test_server import _make_config


def test_recovery_retains_history_and_reopens_only_after_new_failure(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    first = store.record(component="transport", code="failed", severity="error", summary="old",
                         observed_at="2026-09-14T01:00:00+00:00")
    store.record_recovery("transport", "failed", "2026-09-14T02:00:00+00:00", "reconnected")
    assert store.unresolved_count() == 0
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT status, occurrence_count, resolution_note FROM incidents").fetchone() == (
            "resolved", 1, "reconnected")
    assert store.record(component="transport", code="failed", severity="error", summary="new",
                        observed_at="2026-09-14T03:00:00+00:00") == first
    assert store.unresolved()[0]["occurrence_count"] == 2
    # A delayed success from an older connection cannot clear a newer failure.
    store.record_recovery("transport", "failed", "2026-09-14T01:30:00+00:00", "late success")
    assert store.unresolved_count() == 1


def test_fallback_replay_after_recovery_uses_original_occurrence_time(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    store.append_fallback(component="transport", code="failed", severity="error", summary="delayed")
    store.record_recovery("transport", "failed", "2099-01-01T00:00:00+00:00", "new connection")
    restarted = IncidentStore(tmp_path)
    assert restarted.replay_fallback() == 1
    assert restarted.unresolved_count() == 0
    restarted.record(component="other", code="failed", severity="error", summary="unrelated")
    assert restarted.unresolved_count() == 1


def test_core_ready_resolves_prior_fatal_without_hiding_other_faults(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    store.record(component="nirai.core.main", code="core_fatal_error", severity="error", summary="old crash",
                 observed_at="2000-01-01T00:00:00+00:00")
    store.record(component="nirai.core.agent.codex", code="codex_app_server_reader_failed", severity="error", summary="reader failed")
    async def run():
        server = CoreServer(_make_config(tmp_path), port_override=0)
        # Mere CLI availability is not evidence of successful transport recovery.
        server._holo_provider_health = lambda: ({"codex": {"status": "ok"}}, [])
        assert server._holo_health_snapshot()["unresolved_incident_count"] == 2
        await server.start()
        try:
            assert [row["code"] for row in store.unresolved()] == ["codex_app_server_reader_failed"]
        finally:
            await server.stop()
    asyncio.run(run())


def test_staging_ignores_only_root_scratch_and_keeps_nested_runtime(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
    adapter = CursorAcpAdapter(policy)
    for relative in (".tmp-workflow-audit-20260914/fixture.txt", "world/src/renderer/src/runtime/keep.ts",
                     "world/.tmp-source/keep.ts", "core/source.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("source", encoding="utf-8")
    ignored = adapter._writable_staging_ignore_parts(tmp_path, integrated_audit=True)
    stage, baseline = adapter._prepare_staging_workspace("AS-AUDIT", tmp_path, ignore_parts=ignored)
    try:
        assert set(baseline) == {"world/src/renderer/src/runtime/keep.ts", "world/.tmp-source/keep.ts", "core/source.py"}
        assert adapter._workspace_snapshot(stage, ignore_parts=ignored) == baseline
    finally:
        adapter._cleanup_staging_workspace(stage)


def test_staging_permission_failure_is_diagnostic_and_incident_visible(tmp_path: Path, monkeypatch) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
    adapter = CursorAcpAdapter(policy)
    source = tmp_path / "source"
    source.mkdir()
    secret = "DO-NOT-LOG-CONTENTS"
    def fail(*args, **kwargs):
        raise PermissionError(errno.EACCES, secret, str(source / "locked.txt"))
    monkeypatch.setattr(adapter, "_copy_workspace_with_snapshot", fail)
    store = IncidentStore(tmp_path)
    handler = IncidentLogHandler(store)
    logger = logging.getLogger("nirai.core.agent.cursor_acp")
    logger.addHandler(handler)
    try:
        with pytest.raises(AgentRuntimeUnavailableError) as caught:
            adapter._prepare_staging_workspace("AS-FAIL", source)
        assert "errno=13" in str(caught.value)
        assert "source/locked.txt" in str(caught.value)
        assert secret not in str(caught.value)
        assert not (policy.default_workspace_root / ".cursor-stage-AS-FAIL").exists()
        assert store.unresolved()[0]["code"] == "agent_staging_preparation_failed"
        assert secret not in str(store.unresolved())
    finally:
        logger.removeHandler(handler)


def test_copytree_aggregate_diagnostic_redacts_original_messages(tmp_path: Path) -> None:
    exc = shutil.Error([(str(tmp_path / "locked"), "private-target", "[WinError 5] SECRET-VALUE")])
    detail = _staging_os_error_detail(exc, tmp_path, tmp_path / "stage")
    assert "copy_codes=5" in detail and "source/locked" in detail
    assert "SECRET" not in detail and "private-target" not in detail


def test_staging_refuses_recursive_and_ancestor_destinations_before_cleanup(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    marker = source / "keep.txt"
    marker.write_text("keep")
    adapter = CursorAcpAdapter(AgentWorkspacePolicy(tmp_path, ("runtime/workspace",)))
    with pytest.raises(AgentSafetyError, match="recursively"):
        adapter._prepare_staging_workspace("AS-LOOP", source, staging_root=source / "stages")
    assert marker.read_text() == "keep"
    assert not (source / "stages").exists()
    ancestor = tmp_path / ".cursor-stage-AS-ANCESTOR"
    nested = ancestor / "source"
    nested.mkdir(parents=True)
    (nested / "keep").write_text("keep")
    with pytest.raises(AgentSafetyError, match="contains its source"):
        adapter._prepare_staging_workspace("AS-ANCESTOR", nested, staging_root=tmp_path)
    assert (nested / "keep").is_file()


def test_metadata_walk_does_not_silently_ignore_permission_failure(tmp_path: Path, monkeypatch) -> None:
    def walk(*args, **kwargs):
        kwargs["onerror"](PermissionError(errno.EACCES, "denied", str(tmp_path / "source")))
        return iter(())
    monkeypatch.setattr("core.agents.cursor_workspace.os.walk", walk)
    with pytest.raises(PermissionError):
        list(CursorWorkspaceMixin._iter_workspace_files(tmp_path))


def test_stop_conversation_cancels_only_owned_work_started_before_click(tmp_path: Path, monkeypatch) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    monkeypatch.setattr(server._holo_authorization, "require_attached", lambda: None)
    snapshots = [SimpleNamespace(task_id=value) for value in ("T-OLD", "T-NEW", "T-OTHER", "T-NOOWNER")]
    monkeypatch.setattr(server, "_settings_task_snapshots", lambda: snapshots)
    owners = {
        "T-OLD": {"conversation_url": "https://chatgpt.com/c/owner", "created_at": "2026-09-14T01:00:00Z"},
        "T-NEW": {"conversation_url": "https://chatgpt.com/c/owner", "created_at": "2026-09-14T03:00:00Z"},
        "T-OTHER": {"conversation_url": "https://chatgpt.com/c/other", "created_at": "2026-09-14T01:00:00Z"},
    }
    monkeypatch.setattr(server, "_read_holo_task_owner", owners.get)
    cancel = AsyncMock()
    monkeypatch.setattr(server, "_cancel_settings_task", cancel)
    result = asyncio.run(server.holo_stop_conversation_authorized("https://chatgpt.com/c/owner?view=1", "2026-09-14T02:00:00Z"))
    assert result["stopped_task_ids"] == ["T-OLD"]
    cancel.assert_awaited_once_with("T-OLD", abandon_interrupted=True)


def test_successful_world_reconnect_resolves_only_prior_connection_errors(tmp_path: Path, monkeypatch) -> None:
    async def run():
        server = CoreServer(_make_config(tmp_path), port_override=0)
        monkeypatch.setattr(server, "_ensure_usage_polling", lambda: None)
        store = server.incidents
        store.record(component="nirai.core.server", code="world_connection_error", severity="error", summary="old",
                     observed_at="2000-01-01T00:00:00+00:00")
        await server.start()
        try:
            async with connect(f"ws://127.0.0.1:{server.bound_port}") as socket:
                await socket.send(make_message("hello", world_hello_payload(server._world_secret), "hello"))
                response = parse_message(await socket.recv())
                assert response["type"] == "hello_ack"
                assert store.unresolved_count() == 0
                store.record(component="nirai.core.server", code="world_connection_error", severity="error", summary="new")
                assert store.unresolved_count() == 1
        finally:
            await server.stop()
    asyncio.run(run())


def test_conversation_stop_local_client_reaches_authorized_core_endpoint(tmp_path: Path, monkeypatch) -> None:
    async def run():
        secret = "test-local-secret-" * 3
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret=secret)
        monkeypatch.setattr(server._holo_authorization, "require_attached", lambda: None)
        stopped = AsyncMock(return_value={"ok": True, "stopped_task_ids": ["T-OWNED"]})
        monkeypatch.setattr(server, "holo_stop_conversation_authorized", stopped)
        await server.start()
        try:
            descriptor = tmp_path / "test-bridge.json"
            descriptor.write_text(json.dumps({"version": 1, "url": f"ws://127.0.0.1:{server.bound_port}", "secret": secret}))
            process = await asyncio.create_subprocess_exec(
                shutil.which("node"), str(Path(__file__).resolve().parents[2] / "tools" / "holo-local-client.mjs"),
                "conversation-stop", "https://chatgpt.com/c/owner", "2026-09-14T02:00:00Z",
                env={**os.environ, "NIRAI_HOLO_LOCAL_BRIDGE_FILE": str(descriptor)},
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            assert process.returncode == 0, stderr.decode()
            assert json.loads(stdout)["result"]["stopped_task_ids"] == ["T-OWNED"]
            stopped.assert_awaited_once_with("https://chatgpt.com/c/owner", "2026-09-14T02:00:00Z")
        finally:
            await server.stop()
    asyncio.run(run())
