"""Regression locks for findings from Adversarial_Review_2026-09-08.md.

All tests are offline and mutate only pytest temporary directories.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.manager import AgentRuntimeManager
from core.agents.safety import AgentWorkspacePolicy
from core.agents.store import AgentSessionStore
from core.agents.types import AgentSessionSnapshot, utc_now_iso
from core.holo.events import HoloEventQueue
from core.memory.private import PrivateMemoryError, PrivateMemoryService
from core.memory.private_semantic import PrivateVectorStore
from core.memory.structured import WorldStructuredMemoryStore
from core.memory.world import WorldMemoryError, WorldMemoryService
from core.protocol import make_message, parse_message
from core.server import CoreServer
from core.sessions.chat_store import ChatStore
from core.tests.test_server import _make_config


def _snapshot(root: Path, *, session: str = "AS-1", state: str = "running") -> AgentSessionSnapshot:
    return AgentSessionSnapshot(
        task_id="T-1",
        agent_session_id=session,
        resident="Lapan",
        provider="codex",
        working_dir=str(root),
        run_state=state,
        started_at=utc_now_iso(),
        updated_at=utc_now_iso(),
    )


def test_native_turn_keeps_messages_arriving_during_generation_for_next_turn(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        sid = server.sessions.active_session_id
        server.sessions.append_master_say("FIRST", "req-1")

        class Adapter:
            async def run(self, request, *, emit, wait_for_master):
                await emit("run_state", {"provider_session_id": "fake-thread"})
                server.sessions.store.append_entry(
                    sid,
                    kind="holo_say",
                    sender="Holo",
                    text="ARRIVED_DURING_THINK",
                )
                return '{"say":"reply","actions":[],"pass":false,"to":null}'

        server._native_brain._adapters["codex"] = Adapter()

        class Socket:
            async def send(self, raw):
                return None

        await server._respond_to_master(Socket(), "req-1", sid)
        history, *_ = server._native_public_history(sid, "Lapan", "codex")
        texts = [str(item.get("text", "")) for item in history]
        assert "ARRIVED_DURING_THINK" in texts
        assert "reply" not in texts

    asyncio.run(scenario())


def test_cancelled_native_lock_waiter_never_releases_another_turns_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server.sessions.append_master_say("hello", "req")
        sid = server.sessions.active_session_id
        lock = server._native_brain.conversation_lock(server._public_brain_conversation_id(sid, "Lapan"))
        await lock.acquire()
        entered = asyncio.Event()
        original_acquire = lock.acquire

        async def driver_lock_acquire():
            entered.set()
            await original_acquire()

        monkeypatch.setattr(lock, "acquire", driver_lock_acquire)

        class Socket:
            async def send(self, raw):
                return None

        turn = asyncio.create_task(server._respond_to_master(Socket(), "req", sid))
        await asyncio.wait_for(entered.wait(), 2)
        turn.cancel()
        await asyncio.gather(turn, return_exceptions=True)
        assert lock.locked()
        lock.release()

    asyncio.run(scenario())


def test_response_state_closes_when_context_preparation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        messages = []

        class Socket:
            async def send(self, raw):
                messages.append(parse_message(raw))

        async def fail(*args, **kwargs):
            raise OSError("simulated context I/O failure")

        monkeypatch.setattr(server, "_world_memory_context", fail)
        with pytest.raises(OSError):
            await server._respond_to_master(Socket(), "req", server.sessions.active_session_id)
        states = [m["payload"]["active"] for m in messages if m["type"] == "response_state"]
        assert states == [True, False]

    asyncio.run(scenario())


def test_retired_holo_adapter_role_is_explicitly_rejected(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="test-secret",
        )

        class Socket:
            def __init__(self) -> None:
                self.closed: list[tuple[int, str]] = []

            def __aiter__(self):
                async def stream():
                    yield make_message(
                        "hello",
                        {"role": "holo_adapter", "secret": "test-secret"},
                        "hello",
                    )
                return stream()

            async def send(self, raw):
                return None

            async def close(self, *, code: int, reason: str):
                self.closed.append((code, reason))

        socket = Socket()
        await server._handle_connection(socket)
        assert socket.closed == [(4004, "Unsupported Nirai hello role")]

    asyncio.run(scenario())


def test_concurrent_cursor_prepare_keeps_starting_sibling_stage(tmp_path: Path) -> None:
    policy = AgentWorkspacePolicy(tmp_path, ("runtime/workspace",))
    adapter = CursorAcpAdapter(policy)
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("data", encoding="utf-8")
    stage_a, _ = adapter._prepare_staging_workspace("AS-A", source)
    stage_b, _ = adapter._prepare_staging_workspace("AS-B", source)
    try:
        assert stage_a.exists()
        assert stage_b.exists()
    finally:
        adapter._preparing_ids.clear()
        adapter._cleanup_staging_workspace(stage_a)
        adapter._cleanup_staging_workspace(stage_b)


def test_cursor_incomplete_rollback_preserves_recovery_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    working = tmp_path / "runtime" / "workspace" / "T-1"
    working.mkdir(parents=True)
    (working / "a.txt").write_text("ORIGINAL", encoding="utf-8")
    review = tmp_path / "review"
    review.mkdir()
    (review / "a.txt").write_text("CHANGED", encoding="utf-8")
    adapter = CursorAcpAdapter(AgentWorkspacePolicy(tmp_path, ("runtime/workspace",)))
    baseline = adapter._workspace_snapshot(working)

    def fail_copy(source: Path, target: Path) -> None:
        target.write_text("PARTIAL", encoding="utf-8")
        raise OSError("simulated write / rollback error")

    monkeypatch.setattr(adapter, "_atomic_copy_file", fail_copy)
    with pytest.raises(Exception, match="recovery backup preserved"):
        adapter._apply_staged_changes(
            working,
            review,
            baseline,
            [{"relative_path": "a.txt", "change_type": "modify"}],
        )

    recovery_dirs = list((tmp_path / "runtime" / "cursor_recovery").glob("REC-*"))
    assert len(recovery_dirs) == 1
    recovery = recovery_dirs[0]
    assert (recovery / "a.txt").read_text(encoding="utf-8") == "ORIGINAL"
    manifest = json.loads((recovery / "recovery.json").read_text(encoding="utf-8"))
    assert manifest["working_dir"] == str(working)
    assert manifest["rollback_errors"]


def test_cursor_incomplete_rollback_preserves_external_backup_when_recovery_publish_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    working = tmp_path / "runtime" / "workspace" / "T-ROLLBACK-PUBLISH"
    working.mkdir(parents=True)
    (working / "a.txt").write_text("ORIGINAL", encoding="utf-8")
    review = tmp_path / "review-publish-fail"
    review.mkdir()
    (review / "a.txt").write_text("CHANGED", encoding="utf-8")
    adapter = CursorAcpAdapter(AgentWorkspacePolicy(tmp_path, ("runtime/workspace",)))
    baseline = adapter._workspace_snapshot(working)

    def fail_copy(source: Path, target: Path) -> None:
        target.write_text("PARTIAL", encoding="utf-8")
        raise OSError("simulated apply and rollback failure")

    original_replace = os.replace

    def fail_recovery_publish(source: Path | str, target: Path | str) -> None:
        if Path(source).name.startswith(".RB-"):
            raise OSError("simulated recovery publish failure")
        original_replace(source, target)

    monkeypatch.setattr(adapter, "_atomic_copy_file", fail_copy)
    monkeypatch.setattr("core.agents.cursor_acp.os.replace", fail_recovery_publish)

    with pytest.raises(Exception, match="could not be published"):
        adapter._apply_staged_changes(
            working,
            review,
            baseline,
            [{"relative_path": "a.txt", "change_type": "modify"}],
        )

    rollback_dirs = list((tmp_path / "runtime" / "cursor_recovery").glob(".RB-*"))
    assert len(rollback_dirs) == 1
    rollback = rollback_dirs[0] / "a.txt"
    assert rollback.is_file()
    assert rollback.read_text(encoding="utf-8") == "ORIGINAL"
    assert (working / "a.txt").read_text(encoding="utf-8") == "PARTIAL"


def test_parent_and_child_agent_workspaces_conflict(tmp_path: Path) -> None:
    parent = tmp_path / "project"
    child = parent / "src"
    child.mkdir(parents=True)
    manager = AgentRuntimeManager(tmp_path, (str(parent),), adapters={})
    manager._snapshots["AS-1"] = _snapshot(parent)
    assert manager.resource_available(parent) is False
    assert manager.resource_available(child) is False


def test_private_forget_during_embedding_cannot_resurrect_vector(tmp_path: Path) -> None:
    _make_config(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-1",
        sender="master",
        recipient="Lapan",
        text="private fact",
        request_id="r1",
        entry_id="CE-1",
    )
    vectors = PrivateVectorStore(memory, vector_dim=3)
    job = vectors.pending_jobs("Lapan")[0]
    assert memory.forget_entry("Lapan", "CE-1")
    vectors.commit_job(job, [1.0, 0.0, 0.0])
    assert memory.raw_count("Lapan") == 0
    assert vectors.vector_count("Lapan") == 0


def test_agent_append_does_not_rescan_full_event_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = AgentSessionStore(tmp_path)
    current = store.create(_snapshot(tmp_path))
    calls = 0
    original = store.read_events

    def read_all(session_id: str):
        nonlocal calls
        calls += 1
        return original(session_id)

    monkeypatch.setattr(store, "read_events", read_all)
    for i in range(20):
        _, current = store.append_event(current, "status_message", {"text": str(i)})
    assert calls == 0
    assert len(original("AS-1")) == 20


def test_agent_append_preserves_valid_tail_without_newline(tmp_path: Path) -> None:
    store = AgentSessionStore(tmp_path)
    current = store.create(_snapshot(tmp_path))
    _, current = store.append_event(current, "status_message", {"text": "first"})
    log = store.sessions_root / "AS-1" / "events.jsonl"
    log.write_bytes(log.read_bytes().rstrip(b"\n"))
    _, current = store.append_event(current, "status_message", {"text": "second"})
    events = store.read_events("AS-1")
    assert [event["payload"]["text"] for event in events] == ["first", "second"]


def test_deleted_chat_session_id_is_never_reused(tmp_path: Path) -> None:
    store = ChatStore(tmp_path / "chat")
    world = WorldMemoryService(tmp_path)
    first = store.create_session()
    old = store.append_entry(first["id"], kind="say", sender="master", text="OLD SESSION")
    world.record_public_entry(old)
    store.delete_session(first["id"])
    second = store.create_session()
    assert first["id"] != second["id"]
    new = store.append_entry(second["id"], kind="say", sender="master", text="NEW SESSION")
    world.record_public_entry(new)
    assert len(world.raw_entries_for_session(first["id"])) == 1
    assert len(world.raw_entries_for_session(second["id"])) == 1


def test_native_public_bootstrap_reads_only_bounded_recent_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    sid = server.sessions.active_session_id
    for index in range(150):
        server.sessions.store.append_entry(
            sid,
            kind="say",
            sender="master",
            text=f"message-{index}",
        )

    def fail_unbounded(*args, **kwargs):
        raise AssertionError("bootstrap must not materialize the full continuation")

    monkeypatch.setattr(server.sessions, "public_history_after", fail_unbounded)
    history, *_ = server._native_public_history(sid, "Lapan", "codex")
    assert len(history) == 20
    assert history[0]["text"] == "message-130"
    assert history[-1]["text"] == "message-149"


def test_world_memory_duplicate_replay_repairs_missing_legacy_episode(tmp_path: Path) -> None:
    world = WorldMemoryService(tmp_path)
    entry = {
        "entry_id": "CE-EPISODE-REPAIR",
        "ts": "2026-09-08T00:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "repair me",
        "session": "S-20260908-UEPISODE",
    }
    assert world._append_raw_entry(entry) is True
    assert world._episode_path(entry["session"]).exists() is False
    world.record_public_entry(entry)
    episode = world._episode_path(entry["session"]).read_text(encoding="utf-8")
    assert WorldMemoryService.entry_marker(entry) in episode
    assert "repair me" in episode


def test_world_memory_forget_tombstone_blocks_replay_after_crash_before_chat_delete(tmp_path: Path) -> None:
    world = WorldMemoryService(tmp_path)
    entry = {
        "entry_id": "CE-FORGET-CRASH",
        "ts": "2026-09-08T00:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "must stay forgotten",
        "session": "S-20260908-UFORGETCRASH",
    }
    world.record_public_entry(entry)
    assert len(world.raw_entries_for_session(entry["session"])) == 1

    # This is the crash boundary: World Forget committed, but the authoritative
    # Chat JSONL/outbox may still contain and replay the old entry.
    world.forget_session(entry["session"])
    assert world.is_session_forgotten(entry["session"]) is True
    assert world.raw_entries_for_session(entry["session"]) == []

    world.record_public_entry(entry)
    assert world.raw_entries_for_session(entry["session"]) == []
    assert world.episodes_for_session(entry["session"]) == []


def test_world_structured_commits_do_not_resurrect_vectors_after_forget(tmp_path: Path) -> None:
    world = WorldMemoryService(tmp_path)
    entry = {
        "entry_id": "CE-WORLD-VECTOR-RACE",
        "ts": "2026-09-08T00:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "vector race",
        "session": "S-20260908-UVECTORRACE",
    }
    world.record_public_entry(entry)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3, embedding_model="test-embedding")
    job = store.pending_jobs(limit=1)[0]
    with store._connect() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO vector_rebuild_jobs(raw_id) VALUES(?)",
            (job.raw_id,),
        )
        connection.commit()
    rebuild_job = store.pending_vector_rebuild_jobs(limit=1)[0]

    world.forget_session(entry["session"])
    assert store.commit_job(job, embedding=[0.1, 0.2, 0.3], candidates=[]) == 0
    store.commit_vector_rebuild(rebuild_job, [0.1, 0.2, 0.3])

    with store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM raw_vec WHERE raw_id=?",
            (job.raw_id,),
        ).fetchone()[0] == 0


def test_holo_event_pagination_returns_next_cursor_and_high_watermark() -> None:
    async def scenario() -> None:
        queue = HoloEventQueue()
        for i in range(60):
            await queue.publish("example", {"n": i})
        first = await queue.wait_after(0, timeout_sec=0, limit=50, event_epoch=queue.event_epoch)
        assert len(first.events) == 50
        assert first.next_event_id == 50
        assert first.high_watermark_event_id == 60
        second = await queue.wait_after(
            first.next_event_id,
            timeout_sec=0,
            limit=50,
            event_epoch=first.event_epoch,
        )
        assert len(second.events) == 10
        assert second.next_event_id == 60
        assert second.high_watermark_event_id == 60

    asyncio.run(scenario())


def test_holo_event_ring_gap_is_reported_explicitly() -> None:
    async def scenario() -> None:
        queue = HoloEventQueue(max_events=3)
        for i in range(5):
            await queue.publish("example", {"n": i})
        result = await queue.wait_after(
            0,
            timeout_sec=0,
            limit=50,
            event_epoch=queue.event_epoch,
        )
        assert result.gap_detected is True
        assert [event["event_id"] for event in result.events] == [3, 4, 5]
        assert result.next_event_id == 5
        assert result.high_watermark_event_id == 5

    asyncio.run(scenario())


def test_failed_agent_start_converges_to_terminal_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        manager = AgentRuntimeManager(
            tmp_path,
            ("runtime/workspace",),
            adapters={"codex": SimpleNamespace()},
        )

        async def fail(*args, **kwargs):
            raise OSError("simulated event persistence failure")

        monkeypatch.setattr(manager, "_record_event", fail)
        with pytest.raises(OSError):
            await manager.start_session(task_id="T-1", resident="Lapan", provider="codex", prompt="hello")
        assert not manager.has_active_session()
        assert not manager._tasks
        snapshot = manager.list_snapshots()[0]
        assert snapshot.run_state == "failed"
        assert manager.resource_available(tmp_path / "runtime" / "workspace" / "T-1")

    asyncio.run(scenario())


def test_chat_memory_outbox_repairs_cross_store_failure_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _make_config(tmp_path)
        server = CoreServer(config, port_override=0)
        sid = server.sessions.active_session_id

        def fail(entry):
            raise WorldMemoryError("simulated memory disk failure")

        monkeypatch.setattr(server.world_memory, "record_public_entry", fail)

        class Socket:
            def __aiter__(self):
                async def stream():
                    yield make_message(
                        "master_say",
                        {"text": "DURABLE_BUT_NOT_REMEMBERED", "request_id": "r1"},
                    )
                return stream()

            async def send(self, raw):
                return None

        socket = Socket()
        server._world_connection = socket
        await server._handle_connection(socket)
        assert server.sessions.store.pending_memory_sync_count() == 1

        restored = CoreServer(config, port_override=0)
        assert any(
            entry["text"] == "DURABLE_BUT_NOT_REMEMBERED"
            for entry in restored.world_memory.raw_entries_for_session(sid)
        )
        assert restored.sessions.store.pending_memory_sync_count() == 0

    asyncio.run(scenario())


def test_startup_reindexes_fsynced_chat_tail_before_memory_outbox_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config(tmp_path)
    server = CoreServer(config, port_override=0)
    sid = server.sessions.active_session_id

    def fail_index(_session_id: str) -> None:
        raise OSError("simulated power loss before SQLite index/outbox commit")

    monkeypatch.setattr(server.sessions.store, "_ensure_session_indexed", fail_index)
    with pytest.raises(OSError, match="simulated power loss"):
        server.sessions.append_master_say("FSYNC_ONLY_CHAT", "raw-gap-1")

    assert server.world_memory.raw_entries_for_session(sid) == []

    # A fresh Core must index the durable JSONL tail first, create the missing
    # outbox row, and only then replay it into World Memory.
    restored = CoreServer(config, port_override=0)
    assert any(
        entry.get("text") == "FSYNC_ONLY_CHAT"
        for entry in restored.world_memory.raw_entries_for_session(sid)
    )
    assert restored.sessions.store.pending_memory_sync_count() == 0


def test_private_memory_outbox_repairs_whisper_failure_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        config = _make_config(tmp_path)
        server = CoreServer(config, port_override=0)
        sid = server.sessions.active_session_id

        def fail(*args, **kwargs):
            raise PrivateMemoryError("simulated private memory failure")

        monkeypatch.setattr(server.private_memory, "append_whisper", fail)

        class Socket:
            def __aiter__(self):
                async def stream():
                    yield make_message(
                        "master_whisper",
                        {"text": "PRIVATE_DURABLE", "to": "Lapan", "request_id": "rw1"},
                    )
                return stream()

            async def send(self, raw):
                return None

        socket = Socket()
        server._world_connection = socket
        await server._handle_connection(socket)
        assert server.sessions.store.pending_memory_sync_count() == 1

        restored = CoreServer(config, port_override=0)
        whispers = restored.private_memory.recent_whispers("Lapan", 20)
        assert any(entry.get("text") == "PRIVATE_DURABLE" and entry.get("session") == sid for entry in whispers)
        assert restored.sessions.store.pending_memory_sync_count() == 0

    asyncio.run(scenario())


def test_agent_store_tail_snapshot_reads_only_latest_bounded_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = AgentSessionStore(tmp_path)
    session_id = "AS-LONG-EVENT-LOG"
    session_dir = tmp_path / "runtime" / "agent_sessions" / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    events_path = session_dir / "events.jsonl"
    events_path.write_text(
        "".join(
            json.dumps({"seq": seq, "type": "status_message", "payload": {"n": seq}}) + "\n"
            for seq in range(1, 601)
        ),
        encoding="utf-8",
    )

    def forbid_full_scan(_agent_session_id: str):
        raise AssertionError("bounded snapshot must not materialize the full event log")

    monkeypatch.setattr(store, "read_events", forbid_full_scan)
    events = store.read_event_tail(session_id, limit=500)
    assert len(events) == 500
    assert events[0]["seq"] == 101
    assert events[-1]["seq"] == 600


def test_core_agent_snapshot_uses_bounded_event_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    captured: dict[str, object] = {}

    def bounded_snapshot(agent_session_id: str, *, after_seq: int = 0, event_limit: int | None = None):
        captured["agent_session_id"] = agent_session_id
        captured["after_seq"] = after_seq
        captured["event_limit"] = event_limit
        return {
            "session": {
                "agent_session_id": agent_session_id,
                "task_id": "T-LONG",
                "resident": "Lapan",
                "provider": "codex",
                "run_state": "running",
                "working_dir": str(tmp_path),
                "started_at": utc_now_iso(),
                "updated_at": utc_now_iso(),
                "last_event_seq": 600,
                "final_summary": None,
                "pending_request_id": None,
            },
            "events": [
                {"seq": seq, "type": "status_message", "payload": {"n": seq}}
                for seq in range(101, 601)
            ],
            "recovery_options": [],
        }

    monkeypatch.setattr(server.agent_runtime, "snapshot_payload", bounded_snapshot)
    payload = server._agent_snapshot_payload("AS-LONG")
    assert captured["event_limit"] == 500
    assert len(payload["events"]) == 500
    assert payload["events_truncated"] is True
    assert payload["event_window_start_seq"] == 101
    assert payload["last_event_seq"] == 600


def test_holo_event_epoch_recovers_pre_restart_cursor() -> None:
    async def scenario() -> None:
        before_restart = HoloEventQueue()
        for i in range(100):
            await before_restart.publish("example", {"n": i})
        cursor = before_restart.latest_event_id
        old_epoch = before_restart.event_epoch

        after_restart = HoloEventQueue()
        await after_restart.publish("new_event", {"text": "visible immediately"})
        result = await after_restart.wait_after(
            cursor,
            timeout_sec=0,
            event_epoch=old_epoch,
        )
        assert len(result.events) == 1
        assert result.events[0]["type"] == "new_event"
        assert result.cursor_reset is True
        assert result.timed_out is False
        assert result.event_epoch != old_epoch

    asyncio.run(scenario())
