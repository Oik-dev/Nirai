from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
import sqlite3
import time

import pytest

from core import server as server_module
from core.brains.base import BrainUnavailableError
from core.incidents import IncidentLogHandler, IncidentStore, memory_outbox_fingerprint
from core.server import CoreServer
from core.tests.test_server import _make_config


def test_incident_store_dedupes_resolves_and_reopens_without_row_sprawl(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)

    first = store.record(
        component="nirai.core.test",
        code="same_failure",
        severity="error",
        summary="first",
        detail="detail-1",
        error_type="RuntimeError",
    )
    second = store.record(
        component="nirai.core.test",
        code="same_failure",
        severity="error",
        summary="second",
        detail="detail-2",
        error_type="RuntimeError",
    )

    assert second == first
    rows = store.unresolved()
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 2
    assert rows[0]["summary"] == "second"

    assert store.resolve(first, "fixed") is True
    assert store.unresolved_count() == 0

    reopened = store.record(
        component="nirai.core.test",
        code="same_failure",
        severity="error",
        summary="recurred",
        error_type="RuntimeError",
    )
    assert reopened == first
    rows = store.unresolved()
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 3
    assert rows[0]["resolved_at"] is None


def test_error_log_handler_dedupes_same_scope_but_keeps_distinct_provider_failures(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    handler = IncidentLogHandler(store)
    logger = logging.getLogger("nirai.core.test.incident-handler")
    previous_handlers = list(logger.handlers)
    previous_propagate = logger.propagate
    previous_level = logger.level
    try:
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.ERROR)
        logger.error("provider_runtime_failed provider=codex")
        logger.error("provider_runtime_failed provider=codex")
        logger.error("provider_runtime_failed provider=cursor")
    finally:
        logger.handlers = previous_handlers
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)
        handler.close()

    rows = store.unresolved()
    assert len(rows) == 2
    by_summary = {row["summary"]: row for row in rows}
    assert by_summary["provider_runtime_failed provider=codex"]["occurrence_count"] == 2
    assert by_summary["provider_runtime_failed provider=cursor"]["occurrence_count"] == 1


def test_incident_dedupe_never_downgrades_peak_severity(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    incident_id = store.record(
        component="nirai.core.test",
        code="severity_failure",
        severity="critical",
        summary="critical first",
    )
    assert store.record(
        component="nirai.core.test",
        code="severity_failure",
        severity="error",
        summary="error recurrence",
    ) == incident_id

    row = store.unresolved()[0]
    assert row["severity"] == "critical"


def test_incident_sqlite_uses_wal_and_bounded_busy_timeout(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    with store._connect() as connection:
        timeout_ms = int(connection.execute("PRAGMA busy_timeout").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).casefold()
    assert journal_mode == "wal"
    assert 50 <= timeout_ms <= 150


def test_error_log_handler_falls_back_durably_when_incident_sqlite_is_busy(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    handler = IncidentLogHandler(store)
    logger = logging.getLogger("nirai.core.test.incident-busy-fallback")
    previous_handlers = list(logger.handlers)
    previous_propagate = logger.propagate
    previous_level = logger.level
    blocker = sqlite3.connect(store.path, timeout=0.1)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        logger.handlers = [handler]
        logger.propagate = False
        logger.setLevel(logging.ERROR)
        started = time.perf_counter()
        logger.error("provider_runtime_failed provider=codex")
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5
        assert store.fallback_pending() is True
    finally:
        blocker.rollback()
        blocker.close()
        logger.handlers = previous_handlers
        logger.propagate = previous_propagate
        logger.setLevel(previous_level)
        handler.close()

    assert store.replay_fallback() == 1
    assert store.fallback_pending() is False
    rows = store.unresolved(limit=20)
    assert len(rows) == 1
    assert rows[0]["code"] == "provider_runtime_failed"
    assert "provider=codex" in rows[0]["summary"]


def test_incident_fallback_replay_is_bounded_per_health_checkpoint(tmp_path: Path) -> None:
    store = IncidentStore(tmp_path)
    for index in range(40):
        store.append_fallback(
            component="nirai.core.test",
            code=f"fallback_{index}",
            severity="error",
            summary=f"fallback {index}",
        )

    assert store.replay_fallback(limit=32) == 32
    assert store.fallback_pending() is True
    assert store.unresolved_count() == 32
    assert store.replay_fallback(limit=32) == 8
    assert store.fallback_pending() is False
    assert store.unresolved_count() == 40


def test_memory_outbox_fingerprint_keeps_pre_scope_compatibility() -> None:
    legacy_material = "\x1f".join(("nirai.core.memory", "memory_outbox_pending", ""))
    legacy = hashlib.sha256(legacy_material.encode("utf-8")).hexdigest()
    assert memory_outbox_fingerprint() == legacy


def test_holo_dive_health_surfaces_incident_and_allows_resolution(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    server._holo_provider_health = lambda: ({"codex": {"status": "ok", "required_by": ["Lapan"], "detail": "test"}}, [])
    server.holo_open_attach_window("DIVE-INCIDENT")
    server.holo_attach()
    assert server.incidents is not None
    incident_id = server.incidents.record(
        component="nirai.core.test",
        code="repair_me",
        severity="error",
        summary="repair context is waiting",
        detail="stack-like detail",
    )

    snapshot = server.holo_snapshot_authorized()
    health = snapshot["health"]
    assert health["status"] == "attention"
    assert health["unresolved_incident_count"] == 1
    assert health["recent_incidents"][0]["incident_id"] == incident_id
    assert "detail" not in health["recent_incidents"][0]

    repair = server.holo_incidents_authorized()
    assert repair["available"] is True
    assert repair["incidents"][0]["detail"] == "stack-like detail"

    assert server.holo_resolve_incident_authorized(incident_id, "regression passed") is True
    assert server.holo_snapshot_authorized()["health"]["status"] == "ok"


def test_holo_health_flags_missing_runtime_used_by_enabled_resident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)

    def missing_codex() -> tuple[str, ...]:
        raise BrainUnavailableError("Codex missing")

    monkeypatch.setattr(server_module, "resolve_codex_command", missing_codex)
    health = server._holo_health_snapshot()
    assert health["status"] == "attention"
    assert health["missing_required_providers"] == ["codex"]
    assert health["provider_runtime"]["codex"]["status"] == "unavailable"
    assert health["provider_runtime"]["codex"]["required_by"] == ["Lapan"]


def test_holo_health_flags_enabled_resident_with_broken_config(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    (tmp_path / "residents" / "Lapan" / "config.toml").unlink()

    health = server._holo_health_snapshot()

    assert health["status"] == "attention"
    assert health["resident_configuration_errors"]
    assert health["resident_configuration_errors"][0]["resident"] == "Lapan"


def test_holo_health_rejects_empty_quoted_gemini_key(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    resident_config = tmp_path / "residents" / "Lapan" / "config.toml"
    resident_config.write_text('brain = "gemini"\n', encoding="utf-8")
    world = tmp_path / "world"
    world.mkdir(parents=True, exist_ok=True)
    (world / ".env").write_text('GEMINI_API_KEY=""\n', encoding="utf-8")

    health = server._holo_health_snapshot()

    assert server._provider_is_available("gemini") is False
    assert health["status"] == "attention"
    assert health["missing_required_providers"] == ["gemini"]
    assert health["provider_runtime"]["gemini"]["status"] == "unavailable"


def test_holo_health_flags_enabled_resident_without_brain_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "residents" / "Lapan" / "config.toml"
    server = CoreServer(_make_config(tmp_path), port_override=0)
    config_path.write_text('avatar = "lapan/lapan.vrm"\n', encoding="utf-8")

    health = server._holo_health_snapshot()

    assert health["status"] == "attention"
    assert health["resident_configuration_errors"]
    assert health["resident_configuration_errors"][0]["resident"] == "Lapan"
    assert "brain" in health["resident_configuration_errors"][0]["error"].casefold()


def test_corrupt_memory_outbox_payload_self_repairs_from_chat_index(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    server = CoreServer(config, port_override=0)
    session_id = server.sessions.active_session_id
    entry = server.sessions.append_master_say("OUTBOX_CORRUPTION_SOURCE", "outbox-corrupt-1")
    entry_id = str(entry["entry_id"])
    with server.sessions.store._connect_entries() as connection:
        connection.execute(
            "UPDATE memory_outbox SET payload_json='{' WHERE entry_id=?",
            (entry_id,),
        )
        connection.commit()

    restored = CoreServer(config, port_override=0)

    assert restored.sessions.store.pending_memory_sync_count() == 0
    assert any(
        item.get("entry_id") == entry_id
        for item in restored.world_memory.raw_entries_for_session(session_id)
    )
    assert restored.incidents is not None
    assert not any(
        item["code"] == "memory_outbox_unreadable"
        for item in restored.incidents.unresolved(limit=20)
    )


def test_memory_outbox_cannot_promote_private_whisper_to_world_scope(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    server = CoreServer(config, port_override=0)
    session_id = server.sessions.active_session_id
    entry = server.sessions.append_master_whisper("Lapan", "PRIVATE_SCOPE_SOURCE", "scope-corrupt-1")
    entry_id = str(entry["entry_id"])
    tampered = dict(entry)
    tampered["kind"] = "say"
    tampered["text"] = "TAMPERED_PUBLIC_VALUE"
    with server.sessions.store._connect_entries() as connection:
        connection.execute(
            """
            UPDATE memory_outbox
            SET scope='world', resident_name=NULL, payload_json=?
            WHERE entry_id=?
            """,
            (json.dumps(tampered, ensure_ascii=False), entry_id),
        )
        connection.commit()

    restored = CoreServer(config, port_override=0)

    assert restored.sessions.store.pending_memory_sync_count() == 0
    whispers = restored.private_memory.recent_whispers("Lapan", 20)
    assert any(item.get("entry_id") == entry_id and item.get("text") == "PRIVATE_SCOPE_SOURCE" for item in whispers)
    assert not any(
        item.get("entry_id") == entry_id or item.get("text") == "TAMPERED_PUBLIC_VALUE"
        for item in restored.world_memory.raw_entries_for_session(session_id)
    )


def test_unrecoverable_memory_outbox_corruption_does_not_block_core_start(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    server = CoreServer(config, port_override=0)
    entry = server.sessions.append_master_say("OUTBOX_CORRUPTION_SOURCE", "outbox-corrupt-2")
    entry_id = str(entry["entry_id"])
    with server.sessions.store._connect_entries() as connection:
        connection.execute(
            "UPDATE memory_outbox SET payload_json='{' WHERE entry_id=?",
            (entry_id,),
        )
        connection.execute(
            "UPDATE chat_entries SET payload_json='{' WHERE entry_id=?",
            (entry_id,),
        )
        connection.commit()

    restored = CoreServer(config, port_override=0)

    assert restored.sessions.store.pending_memory_sync_count() == 1
    assert restored.incidents is not None
    incidents = restored.incidents.unresolved(limit=20)
    assert any(item["code"] == "memory_outbox_unreadable" for item in incidents)


def test_holo_health_memory_repair_uses_small_interactive_batch(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    server._holo_provider_health = lambda: ({"codex": {"status": "ok", "required_by": ["Lapan"], "detail": "test"}}, [])
    observed_limits: list[int] = []
    original_pending = server.sessions.store.pending_memory_sync

    def pending_memory_sync(*, limit: int = 500):
        observed_limits.append(limit)
        return []

    server.sessions.store.pending_memory_sync = pending_memory_sync  # type: ignore[method-assign]
    try:
        health = server._holo_health_snapshot()
    finally:
        server.sessions.store.pending_memory_sync = original_pending  # type: ignore[method-assign]

    assert health["memory_outbox_pending"] == 0
    assert observed_limits == [32]


def test_holo_attach_result_runs_health_check_automatically(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server._holo_provider_health = lambda: ({"codex": {"status": "ok", "required_by": ["Lapan"], "detail": "test"}}, [])
        server.holo_open_attach_window("DIVE-AUTO-HEALTH")
        assert server.incidents is not None
        server.incidents.record(
            component="nirai.core.test",
            code="auto_visible",
            severity="error",
            summary="visible during attach",
        )

        class Socket:
            def __init__(self) -> None:
                self.messages: list[dict] = []

            async def send(self, raw: str) -> None:
                import json

                self.messages.append(json.loads(raw))

        socket = Socket()
        await server._handle_holo_local_message(
            socket,
            {"type": "holo_attach_request", "payload": {}, "id": "attach-health"},
        )
        result = next(message for message in socket.messages if message.get("id") == "attach-health")
        assert result["payload"]["operation"] == "attach"
        assert result["payload"]["health"]["status"] == "attention"
        assert result["payload"]["health"]["unresolved_incident_count"] == 1

    asyncio.run(scenario())
