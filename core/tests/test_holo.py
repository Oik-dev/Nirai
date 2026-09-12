import asyncio
import json
import os
from pathlib import Path
import shutil
import time

import pytest
from websockets.asyncio.client import connect

from core.agents import AgentEvent, AgentRuntimeManager, AgentRuntimeManagerError, AgentSessionSnapshot
from core.agents.types import utc_now_iso
from core.brains.base import BrainResponse
from core.config import load_config
from core.holo import HoloAuthorization, HoloAuthorizationError, HoloDiveBinding, HoloEventQueue
from core.protocol import make_message, parse_message, world_hello_payload
from core.residents.service import ResidentError
from core.server import CoreServer
from core.usage_budget import UsageBudgetService, parse_codex_rate_limits


class _CaptureWorld:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, raw: str) -> None:
        self.messages.append(parse_message(raw))


class _HoloReviewFakeAdapter:
    provider = "cursor"
    capabilities = frozenset()

    def __init__(self, summary: str = "SAFE\nNo blocking findings") -> None:
        self.summary = summary
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.requests = []
        self.cancelled: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        self.requests.append(request)
        await emit("run_state", {"state": "running"})
        self.started.set()
        await self.release.wait()
        return self.summary

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        self.release.set()
        return True


def _make_config(tmp_path: Path):
    (tmp_path / "config.toml").write_text(
        """
[core]
port = 8765
log_level = "INFO"

[world]
fps = 30
audio_volume = 65
voicevox_url = "http://127.0.0.1:50021"

[ecomode]
resume_delay_sec = 10

[residents]
enabled = ["Lapan"]

[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip(),
        encoding="utf-8",
    )
    resident_dir = tmp_path / "residents" / "Lapan"
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "persona.md").write_text("# Lapan\nprivate persona detail\n", encoding="utf-8")
    (resident_dir / "config.toml").write_text(
        'brain = "codex"\nbrain_model = "secret-model"\navatar = "lapan/lapan.vrm"\nspawn_location = "center"\n',
        encoding="utf-8",
    )
    return load_config(tmp_path)


def test_holo_authorization_requires_master_started_one_shot_dive() -> None:
    now = [1000.0]
    authorization = HoloAuthorization(now=lambda: now[0])

    with pytest.raises(HoloAuthorizationError):
        authorization.attach()

    authorization.open_attach_window("DIVE-1", ttl_sec=300)
    prepared = authorization.prepare_attach()
    assert prepared.dive_session_id == "DIVE-1"
    assert authorization.pending_dive_session_id == "DIVE-1"
    assert authorization.pending_expires_at == 1300.0
    binding = authorization.commit_attach(prepared)
    assert authorization.pending_dive_session_id is None
    assert authorization.require_attached() == binding

    with pytest.raises(HoloAuthorizationError):
        authorization.attach()

    authorization.open_attach_window("DIVE-2", ttl_sec=1)
    now[0] = 1002.0
    with pytest.raises(HoloAuthorizationError):
        authorization.attach()


def test_holo_task_owner_persists_dive_and_conversation_route(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)

    server._persist_holo_task_owner(
        "T-OWNER-1",
        "DIVE-OWNER-1",
        "https://chatgpt.com/c/owner-conversation",
    )

    payload = json.loads(
        (tmp_path / "runtime" / "holo" / "task_owners" / "T-OWNER-1.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload["task_id"] == "T-OWNER-1"
    assert payload["dive_session_id"] == "DIVE-OWNER-1"
    assert payload["conversation_url"] == "https://chatgpt.com/c/owner-conversation"
    assert isinstance(payload["created_at"], str) and payload["created_at"]

    with pytest.raises(AgentRuntimeManagerError):
        server._persist_holo_task_owner("T-OWNER-2", "DIVE-OWNER-2", "https://example.com/c/wrong")


def test_failed_owned_holo_review_auto_resumes_and_acks_durably(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            usage_budget=UsageBudgetService({}),
        )
        task_id = "HR-AUTO-FAIL-1"
        agent_session_id = "AS-HR-AUTO-FAIL-1"
        server._persist_holo_task_owner(
            task_id,
            "DIVE-AUTO-1",
            "https://chatgpt.com/c/auto-review-owner",
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id=task_id,
            agent_session_id=agent_session_id,
            resident="Holo",
            provider="cursor",
            working_dir=str(tmp_path),
            run_state="failed",
            started_at=now,
            updated_at=now,
            read_only=True,
            purpose="review",
        )
        server.agent_runtime.store.create(snapshot)
        server.agent_runtime._snapshots[agent_session_id] = snapshot
        world = _CaptureWorld()
        server._world_connection = world

        await server._broadcast_agent_event(AgentEvent(
            seq=1,
            ts=now,
            task_id=task_id,
            agent_session_id=agent_session_id,
            resident="Holo",
            provider="cursor",
            type="run_state",
            payload={"state": "failed"},
        ))

        auto_resume = [message for message in world.messages if message["type"] == "holo_auto_resume"]
        assert len(auto_resume) == 1
        assert auto_resume[0]["payload"] == {
            "kind": "review",
            "task_id": task_id,
            "agent_session_id": agent_session_id,
            "reason": "failed",
        }
        assert server.agent_runtime._snapshots[agent_session_id].result_notified is False
        assert server._ack_holo_review_auto_resume(task_id, agent_session_id) is True
        assert server.agent_runtime._snapshots[agent_session_id].result_notified is True
        assert server._ack_holo_review_auto_resume(task_id, agent_session_id) is False

    asyncio.run(scenario())


def test_unacked_owned_holo_review_replays_after_world_reconnect(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            usage_budget=UsageBudgetService({}),
        )
        task_id = "HR-AUTO-REPLAY-1"
        agent_session_id = "AS-HR-AUTO-REPLAY-1"
        server._persist_holo_task_owner(
            task_id,
            "DIVE-AUTO-2",
            "https://chatgpt.com/c/auto-review-replay",
        )
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id=task_id,
            agent_session_id=agent_session_id,
            resident="Holo",
            provider="cursor",
            working_dir=str(tmp_path),
            run_state="completed",
            started_at=now,
            updated_at=now,
            read_only=True,
            purpose="review",
            final_summary="SAFE\nNo findings",
        )
        server.agent_runtime.store.create(snapshot)
        server.agent_runtime._snapshots[agent_session_id] = snapshot

        reconnected_world = _CaptureWorld()
        await server._send_pending_holo_review_auto_resumes(reconnected_world)
        assert [message["type"] for message in reconnected_world.messages] == ["holo_auto_resume"]
        assert reconnected_world.messages[0]["payload"]["reason"] == "done"
        assert server.agent_runtime._snapshots[agent_session_id].result_notified is False

        server._ack_holo_review_auto_resume(task_id, agent_session_id)
        after_ack = _CaptureWorld()
        await server._send_pending_holo_review_auto_resumes(after_ack)
        assert after_ack.messages == []

    asyncio.run(scenario())


def test_holo_attach_deadline_is_absolute_across_delayed_delivery(tmp_path: Path) -> None:
    started_at = 1000.0
    deadline_ms = (started_at + 300.0) * 1000.0

    now = [started_at + 299.0]
    server = CoreServer(
        _make_config(tmp_path),
        port_override=0,
        holo_now=lambda: now[0],
    )
    server.holo_open_attach_window(
        "DIVE-LATE",
        attach_expires_at_ms=deadline_ms,
    )
    assert server._holo_authorization.pending_dive_session_id == "DIVE-LATE"

    # The delayed delivery gets only the one second remaining from the
    # Master's original five-minute window, not a fresh five minutes.
    now[0] = started_at + 300.0
    with pytest.raises(HoloAuthorizationError):
        server.holo_attach()

    expired_now = [started_at + 301.0]
    expired = CoreServer(
        _make_config(tmp_path),
        port_override=0,
        holo_now=lambda: expired_now[0],
    )
    with pytest.raises(HoloAuthorizationError):
        expired.holo_open_attach_window(
            "DIVE-TOO-LATE",
            attach_expires_at_ms=deadline_ms,
        )
    assert expired._holo_authorization.pending_dive_session_id is None
    assert expired.holo_addon_state()["current_dive_session_id"] is None


def test_holo_dive_redelivery_after_lost_ack_is_idempotent(tmp_path: Path) -> None:
    now = [1000.0]
    deadline_ms = 1_300_000.0
    server = CoreServer(
        _make_config(tmp_path),
        port_override=0,
        holo_now=lambda: now[0],
    )
    server.holo_open_attach_window("DIVE-ACK-LOST", attach_expires_at_ms=deadline_ms)
    first_binding = server.holo_attach()

    # A reconnect/retry for the same Dive must preserve the consumed binding,
    # not revoke it and open a second one-shot attach opportunity.
    now[0] = 1100.0
    server.holo_open_attach_window("DIVE-ACK-LOST", attach_expires_at_ms=deadline_ms)
    assert server._holo_authorization.binding == first_binding
    assert server.holo_addon_state() == {
        "local_bridge_state": "attached",
        "current_dive_session_id": "DIVE-ACK-LOST",
    }
    with pytest.raises(HoloAuthorizationError):
        server.holo_attach()


def test_holo_binding_write_failure_keeps_pending_deadline_and_allows_retry(tmp_path: Path) -> None:
    now = [1000.0]
    fail_write = [True]

    def write_binding(path: Path, content: str) -> None:
        if fail_write[0]:
            raise OSError("simulated binding write failure")
        path.write_text(content, encoding="utf-8")

    server = CoreServer(
        _make_config(tmp_path),
        port_override=0,
        holo_now=lambda: now[0],
        holo_binding_write_text=write_binding,
    )
    server.holo_open_attach_window(
        "DIVE-WRITE-FAIL",
        attach_expires_at_ms=1_300_000.0,
    )
    original_deadline = server._holo_authorization.pending_expires_at

    with pytest.raises(HoloAuthorizationError, match="could not be saved"):
        server.holo_attach()

    assert server._holo_authorization.binding is None
    assert server._holo_authorization.pending_dive_session_id == "DIVE-WRITE-FAIL"
    assert server._holo_authorization.pending_expires_at == original_deadline == 1300.0
    assert server.holo_addon_state() == {
        "local_bridge_state": "attach_waiting",
        "current_dive_session_id": "DIVE-WRITE-FAIL",
    }
    assert not server._holo_binding_path().exists()

    fail_write[0] = False
    now[0] = 1299.0
    binding = server.holo_attach()
    assert binding.dive_session_id == "DIVE-WRITE-FAIL"
    assert server.holo_addon_state()["local_bridge_state"] == "attached"


def test_holo_binding_replace_failure_does_not_attach_or_restore_after_expiry(tmp_path: Path) -> None:
    now = [1000.0]

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated binding replace failure")

    server = CoreServer(
        _make_config(tmp_path),
        port_override=0,
        holo_now=lambda: now[0],
        holo_binding_replace=fail_replace,
    )
    server.holo_open_attach_window(
        "DIVE-REPLACE-FAIL",
        attach_expires_at_ms=1_300_000.0,
    )

    with pytest.raises(HoloAuthorizationError, match="could not be saved"):
        server.holo_attach()

    assert server._holo_authorization.binding is None
    assert server._holo_authorization.pending_dive_session_id == "DIVE-REPLACE-FAIL"
    assert server._holo_authorization.pending_expires_at == 1300.0
    assert server.holo_addon_state()["local_bridge_state"] == "attach_waiting"
    assert not server._holo_binding_path().exists()
    assert list((tmp_path / "runtime" / "holo").glob("binding.json.*.tmp")) == []

    now[0] = 1301.0
    with pytest.raises(HoloAuthorizationError):
        server.holo_attach()
    assert server.holo_addon_state()["local_bridge_state"] == "not_started"

    (tmp_path / "runtime" / "holo" / "state.json").write_text(
        json.dumps({"current_dive_session_id": "DIVE-REPLACE-FAIL"}),
        encoding="utf-8",
    )
    restarted = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="new-secret")
    with pytest.raises(HoloAuthorizationError):
        restarted.holo_snapshot_authorized()


def test_holo_addon_state_exposes_only_non_secret_lifecycle_facts(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="never-expose")
    assert server.holo_addon_state() == {
        "local_bridge_state": "not_started",
        "current_dive_session_id": None,
    }

    server.holo_open_attach_window("DIVE-STATE")
    assert server.holo_addon_state() == {
        "local_bridge_state": "attach_waiting",
        "current_dive_session_id": "DIVE-STATE",
    }

    server.holo_attach()
    attached = server.holo_addon_state()
    assert attached == {
        "local_bridge_state": "attached",
        "current_dive_session_id": "DIVE-STATE",
    }
    assert "never-expose" not in json.dumps(attached)

def test_holo_binding_survives_core_restart_without_persisting_secret(tmp_path: Path) -> None:
    holo_runtime = tmp_path / "runtime" / "holo"
    holo_runtime.mkdir(parents=True, exist_ok=True)
    (holo_runtime / "state.json").write_text(
        json.dumps({
            "current_dive_url": "https://chatgpt.com/c/test",
            "current_dive_session_id": "DIVE-PERSIST",
            "updated_at": "2026-08-31T09:00:00+09:00",
        }),
        encoding="utf-8",
    )

    first = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret-one")
    first.holo_open_attach_window("DIVE-PERSIST")
    first.holo_attach()

    binding_payload = json.loads((holo_runtime / "binding.json").read_text(encoding="utf-8"))
    assert binding_payload["dive_session_id"] == "DIVE-PERSIST"
    assert set(binding_payload) == {"dive_session_id", "attached_at"}
    assert "secret-one" not in json.dumps(binding_payload)

    restarted = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret-two")
    snapshot = restarted.holo_snapshot_authorized()
    assert snapshot["active_session"] is not None
    assert restarted._holo_authorization.binding is not None
    assert restarted._holo_authorization.binding.dive_session_id == "DIVE-PERSIST"


def test_holo_binding_is_not_restored_for_different_current_dive(tmp_path: Path) -> None:
    holo_runtime = tmp_path / "runtime" / "holo"
    holo_runtime.mkdir(parents=True, exist_ok=True)
    (holo_runtime / "state.json").write_text(
        json.dumps({"current_dive_session_id": "DIVE-NEW"}),
        encoding="utf-8",
    )
    (holo_runtime / "binding.json").write_text(
        json.dumps({"dive_session_id": "DIVE-OLD", "attached_at": 1.0}),
        encoding="utf-8",
    )

    restarted = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
    with pytest.raises(HoloAuthorizationError):
        restarted.holo_snapshot_authorized()


def test_world_dive_message_opens_one_time_holo_attach_window(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [1000.0]
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
            holo_now=lambda: now[0],
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", world_hello_payload(server._world_secret), "hello-dive"))
                hello = parse_message(await websocket.recv())
                assert hello["type"] == "hello_ack"
                assert hello["payload"]["holo_addon"]["local_bridge_state"] == "not_started"
                await websocket.send(make_message(
                    "holo_dive_started",
                    {
                        "dive_session_id": "DIVE-FROM-UI",
                        "attach_expires_at_ms": 1_300_000.0,
                    },
                    "dive-start",
                ))
                waiting = parse_message(await websocket.recv())
                assert waiting["type"] == "holo_addon_state"
                assert waiting["id"] == "dive-start"
                assert waiting["payload"] == {
                    "local_bridge_state": "attach_waiting",
                    "current_dive_session_id": "DIVE-FROM-UI",
                }
                await websocket.send(make_message("holo_addon_state_request", {}, "holo-state"))
                refreshed = parse_message(await websocket.recv())
                assert refreshed["type"] == "holo_addon_state"
                assert refreshed["id"] == "holo-state"
                await websocket.send(make_message("chat_session_list_request", {}, "sync-after-dive"))
                while True:
                    synced = parse_message(await websocket.recv())
                    if synced["type"] == "chat_session_list" and synced.get("id") == "sync-after-dive":
                        break
                assert server._holo_authorization.pending_dive_session_id == "DIVE-FROM-UI"
                assert server.holo_attach().dive_session_id == "DIVE-FROM-UI"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_world_dive_message_does_not_reopen_after_absolute_deadline(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = [1301.0]
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
            holo_now=lambda: now[0],
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", world_hello_payload(server._world_secret), "hello-expired"))
                await websocket.recv()
                await websocket.send(make_message(
                    "holo_dive_started",
                    {
                        "dive_session_id": "DIVE-EXPIRED",
                        "attach_expires_at_ms": 1_300_000.0,
                    },
                    "expired-retry",
                ))
                response = parse_message(await websocket.recv())
                assert response["type"] == "holo_addon_state"
                assert response["id"] == "expired-retry"
                assert response["payload"] == {
                    "local_bridge_state": "not_started",
                    "current_dive_session_id": None,
                }
                assert server._holo_authorization.pending_dive_session_id is None
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_holo_local_connection_requires_per_process_secret_and_serves_only_holo_operations(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.holo_open_attach_window("DIVE-LOCAL")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None

            async with connect(f"ws://127.0.0.1:{port}") as rejected:
                await rejected.send(make_message(
                    "hello", {"role": "holo_local", "secret": "wrong"}, "bad-hello"
                ))
                await rejected.wait_closed()
                assert rejected.close_code == 4003

            async with connect(f"ws://127.0.0.1:{port}") as local:
                await local.send(make_message(
                    "hello", {"role": "holo_local", "secret": "local-secret"}, "hello"
                ))
                hello = parse_message(await local.recv())
                assert hello["type"] == "holo_local_hello_ack"

                await local.send(make_message("holo_attach_request", {}, "attach"))
                attached = parse_message(await local.recv())
                assert attached["type"] == "holo_local_result"
                assert attached["payload"]["ok"] is True
                assert attached["payload"]["dive_session_id"] == "DIVE-LOCAL"

                await local.send(make_message("holo_snapshot_request", {}, "snapshot"))
                snapshot = parse_message(await local.recv())
                assert snapshot["payload"]["ok"] is True

                await local.send(make_message(
                    "holo_world_say_request",
                    {"text": "local hello", "to": "Lapan"},
                    "say",
                ))
                said = parse_message(await local.recv())
                assert said["payload"]["entry"]["kind"] == "holo_say"

                await local.send(make_message("resident_delete", {"name": "Lapan"}, "forbidden"))
                forbidden = parse_message(await local.recv())
                assert forbidden["payload"]["ok"] is False
                assert server.resident_service.enabled_names == ("Lapan",)
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_holo_attach_persistence_failure_returns_structured_error_and_keeps_world_waiting(tmp_path: Path) -> None:
    async def scenario() -> None:
        def fail_write(_path: Path, _content: str) -> None:
            raise OSError("simulated binding write failure")

        now = [1000.0]
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="local-secret",
            holo_now=lambda: now[0],
            holo_binding_write_text=fail_write,
        )
        server.holo_open_attach_window(
            "DIVE-PERSIST-ERROR",
            attach_expires_at_ms=1_300_000.0,
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as world:
                await world.send(make_message("hello", world_hello_payload(server._world_secret), "world-hello"))
                hello = parse_message(await world.recv())
                assert hello["payload"]["holo_addon"] == {
                    "local_bridge_state": "attach_waiting",
                    "current_dive_session_id": "DIVE-PERSIST-ERROR",
                }

                async with connect(f"ws://127.0.0.1:{port}") as local:
                    await local.send(make_message(
                        "hello", {"role": "holo_local", "secret": "local-secret"}, "local-hello"
                    ))
                    await local.recv()
                    await local.send(make_message("holo_attach_request", {}, "attach-fails"))
                    failed = parse_message(await local.recv())
                    assert failed["type"] == "holo_local_result"
                    assert failed["id"] == "attach-fails"
                    assert failed["payload"]["operation"] == "holo_attach_request"
                    assert failed["payload"]["ok"] is False
                    assert "could not be saved" in failed["payload"]["error"]

                    world_state = parse_message(await world.recv())
                    assert world_state["type"] == "holo_addon_state"
                    assert world_state["payload"] == {
                        "local_bridge_state": "attach_waiting",
                        "current_dive_session_id": "DIVE-PERSIST-ERROR",
                    }
                    assert server._holo_authorization.binding is None
                    assert server._holo_authorization.pending_expires_at == 1300.0

                    # The Local connection remains usable and reports the
                    # authorization failure instead of being torn down.
                    await local.send(make_message("holo_snapshot_request", {}, "snapshot-after-fail"))
                    snapshot = parse_message(await local.recv())
                    assert snapshot["id"] == "snapshot-after-fail"
                    assert snapshot["payload"]["ok"] is False
                    assert local.close_code is None
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_holo_local_disconnect_cancels_event_wait(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.holo_open_attach_window("DIVE-CANCEL")
        server.holo_attach()
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as local:
                await local.send(make_message(
                    "hello", {"role": "holo_local", "secret": "local-secret"}, "hello"
                ))
                await local.recv()
                await local.send(make_message(
                    "holo_wait_events_request",
                    {"after_event_id": server._holo_events.latest_event_id, "timeout_sec": 5, "limit": 50},
                    "wait",
                ))
                for _ in range(50):
                    if server._holo_events.active_waiters == 1:
                        break
                    await asyncio.sleep(0.002)
                assert server._holo_events.active_waiters == 1
                await local.close()

            for _ in range(50):
                if server._holo_events.active_waiters == 0:
                    break
                await asyncio.sleep(0.002)
            assert server._holo_events.active_waiters == 0
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_usage_force_requirement_bypasses_cached_snapshot_after_provider_limit(tmp_path: Path) -> None:
    class MutableUsageProvider:
        provider = "codex"

        def __init__(self) -> None:
            self.snapshot = parse_codex_rate_limits({
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 10, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                    "secondary": {"usedPercent": 20, "windowDurationMins": 10_080, "resetsAt": 1_800_604_800},
                }
            })
            self.calls = 0
            self.fail = False

        async def fetch(self):
            self.calls += 1
            if self.fail:
                raise RuntimeError("usage endpoint unavailable")
            return self.snapshot

    async def scenario() -> None:
        provider = MutableUsageProvider()
        service = UsageBudgetService({"codex": provider}, refresh_interval_seconds=300)
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            usage_budget=service,
        )
        await server._refresh_usage_budget({"codex"}, force=True)
        assert provider.calls == 1
        assert service.snapshot("codex") is not None
        assert service.snapshot("codex").status == "available"

        server._usage_force_refresh_required.add("codex")
        provider.fail = True
        await server._refresh_usage_budget({"codex"})
        assert provider.calls == 2
        assert service.snapshot("codex").status == "unknown"
        assert service.snapshot("codex").stale is True
        assert "codex" in server._usage_force_refresh_required
        assert server._resident_usage_hard_limited(server.resident_service.load("Lapan")) is True

        provider.fail = False
        provider.snapshot = parse_codex_rate_limits({
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                "secondary": {"usedPercent": 30, "windowDurationMins": 10_080, "resetsAt": 1_800_604_800},
            }
        })
        await server._refresh_usage_budget({"codex"})
        assert provider.calls == 3
        assert service.snapshot("codex").status == "available"
        assert "codex" not in server._usage_force_refresh_required

    asyncio.run(scenario())


def test_holo_supervisor_review_requires_owning_dive_and_conversation(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.holo_open_attach_window("DIVE-REVIEW-OWNER")
        server.holo_attach()
        with pytest.raises(AgentRuntimeManagerError, match="owning Dive"):
            await server.holo_start_cursor_review_authorized(
                tmp_path.name,
                "Review without owner must fail",
            )

    asyncio.run(scenario())


def test_integrated_audit_uses_auditor_at_low_remaining_but_never_crosses_hard_limit(tmp_path: Path) -> None:
    class MutableUsageProvider:
        provider = "codex"

        def __init__(self) -> None:
            self.snapshot = parse_codex_rate_limits({
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                    "secondary": {"usedPercent": 70, "windowDurationMins": 10_080, "resetsAt": 1_800_604_800},
                }
            })
            self.calls = 0

        async def fetch(self):
            self.calls += 1
            return self.snapshot

    async def scenario() -> None:
        usage = MutableUsageProvider()
        adapter = _HoloReviewFakeAdapter("Integrated audit completed")
        adapter.provider = "codex"
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="local-secret",
            usage_budget=UsageBudgetService({"codex": usage}),
        )
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"codex": adapter},
            broadcast=server._broadcast_agent_event,
        )
        supports = server._resident_supports_agent_work
        server.resident_service.set_role("Lapan", "integrated_auditor", can_execute=supports)
        server.resident_service.create("Worker", "codex")
        server.resident_service.set_role("Worker", "executor", can_execute=supports)
        server.holo_open_attach_window("DIVE-IA")
        server.holo_attach()

        assert [resident.name for resident in server._integrated_audit_candidates()] == ["Lapan"]
        task = await server.holo_start_integrated_audit_authorized(
            "Audit the completed development unit and fix issues you find.",
            target_name=tmp_path.name,
            dive_session_id="DIVE-IA",
            conversation_url="https://chatgpt.com/c/integrated-audit-owner",
        )
        assert task["task_id"].startswith("IA-")
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        request = adapter.requests[-1]
        assert request.resident == "Lapan"
        assert request.purpose == "integrated_audit"
        assert request.read_only is False
        assert request.working_dir == tmp_path.resolve()
        assert usage.calls >= 1

        adapter.release.set()
        done, timed_out = await server.holo_wait_task_authorized(task["task_id"], timeout_sec=1)
        assert timed_out is False
        assert done["state"] == "completed"
        audit_state = server.holo_snapshot()["integrated_audit"]
        assert audit_state["last_completed"]["task_id"] == task["task_id"]
        assert audit_state["cadence_reference_seconds"] == 18_000

        usage.snapshot = parse_codex_rate_limits({
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                "secondary": {"usedPercent": 70, "windowDurationMins": 10_080, "resetsAt": 1_800_604_800},
                "rateLimitReachedType": "primary",
            }
        })
        with pytest.raises(AgentRuntimeManagerError, match="hard limit"):
            await server.holo_start_integrated_audit_authorized(
                "Master explicitly allows remaining quota, but hard limit still wins.",
                target_name=tmp_path.name,
                dive_session_id="DIVE-IA",
                conversation_url="https://chatgpt.com/c/integrated-audit-owner",
            )

    asyncio.run(scenario())


def test_holo_supervisor_cursor_review_waits_for_terminal_and_returns_structured_verdict(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _HoloReviewFakeAdapter("NEEDS FIX\n[P1] core/server.py: review finding")
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        server.holo_open_attach_window("DIVE-REVIEW")
        server.holo_attach()

        review = await server.holo_start_cursor_review_authorized(
            tmp_path.name,
            "Review the Holo supervisor integration",
            dive_session_id="DIVE-REVIEW",
            conversation_url="https://chatgpt.com/c/review-owner",
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)

        assert review["task_id"].startswith("HR-")
        assert review["target"] == tmp_path.name
        assert review["terminal"] is False
        assert len(adapter.requests) == 1
        assert adapter.requests[0].read_only is True
        assert adapter.requests[0].working_dir == tmp_path.resolve()
        assert adapter.requests[0].resident == "Holo"

        pending, timed_out = await server.holo_wait_cursor_review_authorized(
            review["agent_session_id"],
            timeout_sec=0,
        )
        assert timed_out is True
        assert pending["terminal"] is False
        assert pending["verdict"] == "UNKNOWN"

        dispatch_calls: list[bool] = []
        server._schedule_task_queue_dispatch = lambda: dispatch_calls.append(True)  # type: ignore[method-assign]
        adapter.release.set()
        completed, timed_out = await server.holo_wait_cursor_review_authorized(
            review["agent_session_id"],
            timeout_sec=1,
        )

        assert timed_out is False
        assert completed["terminal"] is True
        assert completed["state"] == "completed"
        assert completed["verdict"] == "NEEDS_FIX"
        assert completed["final_summary"].startswith("NEEDS FIX")
        assert dispatch_calls

        normal = await server.agent_runtime.start_session(
            task_id="TASK-NORMAL-NOT-HOLO-REVIEW",
            resident="Cursor",
            provider="cursor",
            prompt="normal task",
        )
        with pytest.raises(HoloAuthorizationError, match="not a Holo-supervised"):
            server._holo_review_snapshot(normal.agent_session_id)

    asyncio.run(scenario())


def test_holo_supervisor_cursor_review_cancel_is_limited_to_its_review_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _HoloReviewFakeAdapter()
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        server.holo_open_attach_window("DIVE-REVIEW-CANCEL")
        server.holo_attach()

        review = await server.holo_start_cursor_review_authorized(
            tmp_path.name,
            "Review and wait",
            dive_session_id="DIVE-REVIEW-CANCEL",
            conversation_url="https://chatgpt.com/c/review-cancel-owner",
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        result = await server.holo_cancel_cursor_review_authorized(review["agent_session_id"])
        assert result["cancellation_requested"] is True
        assert adapter.cancelled == [review["agent_session_id"]]

        terminal, timed_out = await server.holo_wait_cursor_review_authorized(
            review["agent_session_id"],
            timeout_sec=1,
        )
        assert timed_out is False
        assert terminal["state"] == "cancelled"
        assert terminal["verdict"] == "UNKNOWN"

    asyncio.run(scenario())


def test_holo_review_interrupted_after_core_restart_is_not_a_finished_verdict(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bootstrap = CoreServer(config, port_override=0, holo_local_secret="local-secret")
    now = utc_now_iso()
    metadata_dir = bootstrap.agent_runtime.workspace_policy.task_metadata_dir("HR-RESTART-REVIEW")
    (metadata_dir / "task.md").write_text("restart review task\n", encoding="utf-8")
    bootstrap.agent_runtime.store.create(AgentSessionSnapshot(
        task_id="HR-RESTART-REVIEW",
        agent_session_id="AS-HR-RESTART",
        resident="Holo",
        provider="cursor",
        working_dir=str(tmp_path),
        run_state="running",
        started_at=now,
        updated_at=now,
        read_only=True,
        purpose="review",
        origin_chat_session_id=None,
        task_phase="running",
    ))

    recovered = CoreServer(config, port_override=0, holo_local_secret="local-secret")
    recovered.holo_open_attach_window("DIVE-REVIEW-RESTART")
    recovered.holo_attach()
    snapshot = recovered._holo_review_snapshot("AS-HR-RESTART")
    assert snapshot["state"] == "interrupted"
    assert snapshot["terminal"] is False
    assert snapshot["verdict"] == "UNKNOWN"
    assert snapshot["recovery_options"] == ["rerun", "abandon"]

    async def scenario() -> None:
        pending, timed_out = await recovered.holo_wait_cursor_review_authorized(
            "AS-HR-RESTART",
            timeout_sec=0,
        )
        assert timed_out is True
        assert pending["terminal"] is False
        assert pending["state"] == "interrupted"

        adapter = _HoloReviewFakeAdapter("SAFE\nRecovered review completed")
        recovered.agent_runtime = AgentRuntimeManager(
            tmp_path,
            recovered.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=recovered._broadcast_agent_event,
        )
        result = await recovered.holo_recover_cursor_review_authorized(
            "AS-HR-RESTART",
            "rerun",
        )
        child_id = result["review"]["agent_session_id"]
        assert child_id != "AS-HR-RESTART"
        assert result["action"] == "rerun"
        assert result["source_review"]["state"] == "cancelled"
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        assert adapter.requests[-1].read_only is True
        assert adapter.requests[-1].purpose == "review"
        adapter.release.set()
        completed, timed_out = await recovered.holo_wait_cursor_review_authorized(
            child_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert completed["state"] == "completed"
        assert completed["verdict"] == "SAFE"

    asyncio.run(scenario())


def test_holo_review_uses_resource_policy_instead_of_global_agent_fifo(tmp_path: Path) -> None:
    async def scenario() -> None:
        review_adapter = _HoloReviewFakeAdapter()
        work_adapter = _HoloReviewFakeAdapter()
        work_adapter.provider = "codex"
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": review_adapter, "codex": work_adapter},
            broadcast=server._broadcast_agent_event,
        )
        server.holo_open_attach_window("DIVE-REVIEW-RESOURCE")
        server.holo_attach()

        metadata_dir = server.agent_runtime.workspace_policy.task_metadata_dir("TASK-RESOURCE-HOLD")
        work = await server.agent_runtime.start_session(
            task_id="TASK-RESOURCE-HOLD",
            resident="Lapan",
            provider="codex",
            prompt="keep a runtime Task workspace occupied",
            task_metadata_dir=str(metadata_dir),
        )
        await asyncio.wait_for(work_adapter.started.wait(), timeout=0.5)
        assert server.agent_runtime.has_active_session() is True

        review = await server.holo_start_cursor_review_authorized(
            tmp_path.name,
            "Review while independent Agent work is running",
            dive_session_id="DIVE-REVIEW-RESOURCE",
            conversation_url="https://chatgpt.com/c/review-resource-owner",
        )
        await asyncio.wait_for(review_adapter.started.wait(), timeout=0.5)

        assert review["task_id"].startswith("HR-")
        assert review["terminal"] is False
        assert server.agent_runtime.snapshot_payload(work.agent_session_id)["session"]["run_state"] == "running"

        review_adapter.release.set()
        work_adapter.release.set()
        await server.holo_wait_cursor_review_authorized(
            review["agent_session_id"],
            timeout_sec=1,
        )

    asyncio.run(scenario())


def test_holo_local_client_end_to_end(tmp_path: Path) -> None:
    async def run_client(nirai_root: Path, env: dict[str, str], *args: str) -> dict:
        client = await asyncio.create_subprocess_exec(
            "node.exe",
            str(nirai_root / "tools" / "holo-local-client.mjs"),
            *args,
            cwd=nirai_root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(client.communicate(), timeout=20)
        assert client.returncode == 0, stderr.decode("utf-8", errors="replace")
        lines = [line for line in stdout.decode("utf-8").splitlines() if line.strip()]
        assert lines
        return json.loads(lines[-1])

    async def scenario() -> None:
        secret = "x" * 64
        skill_dir = tmp_path / "skills" / "sample-holo"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: sample-holo\ndescription: Holo Skill経路の確認。\n---\n\n# Sample\n必要な時だけ使う。\n",
            encoding="utf-8",
        )
        review_adapter = _HoloReviewFakeAdapter("SAFE\nLocal client review completed")
        work_adapter = _HoloReviewFakeAdapter("Local client task completed")
        work_adapter.provider = "codex"
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret=secret,
            usage_budget=UsageBudgetService({}),
        )
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": review_adapter, "codex": work_adapter},
            broadcast=server._broadcast_agent_event,
        )
        server._holo_provider_health = lambda: ({"codex": {"status": "ok", "required_by": ["Lapan"], "detail": "test"}}, [])
        server.holo_open_attach_window("DIVE-CLIENT")
        assert server.incidents is not None
        incident_id = server.incidents.record(
            component="nirai.core.test",
            code="client_repair",
            severity="error",
            summary="client repair context",
            detail="client detail",
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            bridge_file = tmp_path / "private" / "holo-local-bridge.json"
            bridge_file.parent.mkdir(parents=True)
            bridge_file.write_text(json.dumps({
                "version": 1,
                "url": f"ws://127.0.0.1:{port}",
                "secret": secret,
                "server_pid": 1234,
            }), encoding="utf-8")
            env = {**os.environ, "NIRAI_HOLO_LOCAL_BRIDGE_FILE": str(bridge_file)}
            nirai_root = tmp_path
            client_source = Path(__file__).resolve().parents[2] / "tools" / "holo-local-client.mjs"
            (nirai_root / "tools").mkdir()
            shutil.copyfile(client_source, nirai_root / "tools" / "holo-local-client.mjs")
            holo_state = nirai_root / "runtime" / "holo" / "state.json"
            holo_state.parent.mkdir(parents=True, exist_ok=True)
            holo_state.write_text(json.dumps({
                "current_dive_session_id": "DIVE-CLIENT",
                "current_dive_url": "https://chatgpt.com/c/local-client-test",
            }), encoding="utf-8")

            attached = await run_client(nirai_root, env, "attach")
            assert attached["result"]["dive_session_id"] == "DIVE-CLIENT"
            assert attached["result"]["health"]["status"] == "attention"
            assert attached["result"]["health"]["unresolved_incident_count"] == 1
            assert secret not in json.dumps(attached)

            incidents = await run_client(nirai_root, env, "incidents", "20")
            assert incidents["result"]["available"] is True
            assert incidents["result"]["incidents"][0]["incident_id"] == incident_id
            assert incidents["result"]["incidents"][0]["detail"] == "client detail"
            resolved = await run_client(
                nirai_root,
                env,
                "incident-resolve",
                incident_id,
                "fixed by Holo",
            )
            assert resolved["result"]["resolved"] is True

            before = await run_client(nirai_root, env, "snapshot")
            assert before["result"]["snapshot"]["health"]["status"] == "ok"
            cursor = before["result"]["snapshot"]["latest_event_id"]
            assert secret not in json.dumps(before)

            skills = await run_client(nirai_root, env, "skills")
            assert skills["result"]["count"] == 1
            assert skills["result"]["skills"][0]["name"] == "sample-holo"
            assert "Holo Skill経路の確認。" in skills["result"]["skills"][0]["description"]
            assert "content" not in skills["result"]["skills"][0]
            assert secret not in json.dumps(skills)

            task_targets = await run_client(nirai_root, env, "task-targets")
            assert task_targets["result"]["targets"] == []
            assert secret not in json.dumps(task_targets)

            said = await run_client(nirai_root, env, "say", "client hello", "Lapan")
            assert said["result"]["entry"]["kind"] == "holo_say"
            assert secret not in json.dumps(said)

            waited = await run_client(nirai_root, env, "wait", str(cursor), "1", "50")
            assert waited["result"]["timed_out"] is False
            assert any(
                event.get("payload", {}).get("entry", {}).get("text") == "client hello"
                for event in waited["result"]["events"]
            )
            assert secret not in json.dumps(waited)

            task_started = await run_client(
                nirai_root,
                env,
                "task-start",
                "-",
                "Lapan",
                "Implement the requested change",
            )
            task_id = task_started["result"]["task"]["task_id"]
            assert task_id.startswith("T-")
            assert task_started["result"]["task"]["resident"] == "Lapan"
            assert secret not in json.dumps(task_started)
            await asyncio.wait_for(work_adapter.started.wait(), timeout=0.5)

            task_snapshot = await run_client(nirai_root, env, "task-snapshot", task_id)
            agent_session_id = task_snapshot["result"]["task"]["agent_session_id"]
            assert task_snapshot["result"]["task"]["state"] == "running"
            assert task_snapshot["result"]["task"]["provider"] == "codex"
            assert secret not in json.dumps(task_snapshot)

            task_pending = await run_client(nirai_root, env, "task-wait", task_id, "0")
            assert task_pending["result"]["timed_out"] is True
            assert task_pending["result"]["task"]["agent_session_id"] == agent_session_id

            work_adapter.release.set()
            task_done = await run_client(nirai_root, env, "task-wait", task_id, "1")
            assert task_done["result"]["timed_out"] is False
            assert task_done["result"]["task"]["state"] == "completed"
            assert task_done["result"]["task"]["final_summary"] == "Local client task completed"
            assert secret not in json.dumps(task_done)

            server.resident_service.set_role(
                "Lapan",
                "integrated_auditor",
                can_execute=server._resident_supports_agent_work,
            )
            class SlowAuditUsageProvider:
                provider = "codex"

                async def fetch(self):
                    # Still within the usage fetch deadline, but longer than
                    # an ordinary local command's old ten-second deadline.
                    await asyncio.sleep(10.2)
                    return parse_codex_rate_limits({"rateLimits": {
                        "primary": {"usedPercent": 20, "windowDurationMins": 300},
                    }})

            server.usage_budget.register(SlowAuditUsageProvider())
            audit_started = await run_client(
                nirai_root,
                env,
                "audit-start",
                tmp_path.name,
                "Audit the completed unit and fix issues if needed",
            )
            audit_task_id = audit_started["result"]["task"]["task_id"]
            assert audit_task_id.startswith("IA-")
            audit_done = await run_client(nirai_root, env, "task-wait", audit_task_id, "1")
            assert audit_done["result"]["timed_out"] is False
            assert audit_done["result"]["task"]["state"] == "completed"
            assert work_adapter.requests[-1].purpose == "integrated_audit"
            assert work_adapter.requests[-1].read_only is False
            assert secret not in json.dumps(audit_done)

            review_started = await run_client(
                nirai_root,
                env,
                "review",
                tmp_path.name,
                "Review the current implementation",
            )
            review = review_started["result"]["review"]
            assert review["task_id"].startswith("HR-")
            assert review["target"] == tmp_path.name
            assert review["terminal"] is False
            assert secret not in json.dumps(review_started)
            await asyncio.wait_for(review_adapter.started.wait(), timeout=0.5)

            review_pending = await run_client(
                nirai_root,
                env,
                "review-wait",
                review["agent_session_id"],
                "0",
            )
            assert review_pending["result"]["timed_out"] is True
            assert review_pending["result"]["review"]["terminal"] is False

            review_adapter.release.set()
            review_done = await run_client(
                nirai_root,
                env,
                "review-wait",
                review["agent_session_id"],
                "1",
            )
            assert review_done["result"]["timed_out"] is False
            assert review_done["result"]["review"]["state"] == "completed"
            assert review_done["result"]["review"]["verdict"] == "SAFE"
            assert review_done["result"]["review"]["final_summary"].startswith("SAFE")
            assert secret not in json.dumps(review_done)

            recover_task_id = "HR-CLIENT-RECOVER"
            recover_metadata = server.agent_runtime.workspace_policy.task_metadata_dir(recover_task_id)
            (recover_metadata / "task.md").write_text("recover local review\n", encoding="utf-8")
            now = utc_now_iso()
            recover_source = AgentSessionSnapshot(
                task_id=recover_task_id,
                agent_session_id="AS-HR-CLIENT-RECOVER",
                resident="Holo",
                provider="cursor",
                working_dir=str(tmp_path),
                run_state="interrupted",
                started_at=now,
                updated_at=now,
                read_only=True,
                purpose="review",
            )
            server.agent_runtime.store.create(recover_source)
            server.agent_runtime._snapshots[recover_source.agent_session_id] = recover_source
            recovered_review = await run_client(
                nirai_root,
                env,
                "review-recover",
                recover_source.agent_session_id,
                "rerun",
            )
            assert recovered_review["result"]["action"] == "rerun"
            assert recovered_review["result"]["source_review"]["state"] == "cancelled"
            assert recovered_review["result"]["review"]["task_id"] == recover_task_id
            assert review_adapter.requests[-1].read_only is True
            assert review_adapter.requests[-1].purpose == "review"
            assert secret not in json.dumps(recovered_review)
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_holo_local_task_response_cannot_decide_approval_or_plan(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="local-secret")
        server.holo_open_attach_window("DIVE-DECISION-BOUNDARY")
        server.holo_attach()
        task_id = "T-HOLO-DECISION-BOUNDARY"
        metadata_dir = server.agent_runtime.workspace_policy.task_metadata_dir(task_id)
        (metadata_dir / "task.md").write_text("decision boundary\n", encoding="utf-8")
        now = utc_now_iso()
        snapshot = AgentSessionSnapshot(
            task_id=task_id,
            agent_session_id="AS-HOLO-DECISION-BOUNDARY",
            resident="Lapan",
            provider="cursor",
            working_dir=str(metadata_dir),
            run_state="waiting_for_master",
            started_at=now,
            updated_at=now,
            origin_chat_session_id=server.sessions.active_session_id,
            pending_request_id="REQ-DECISION",
            pending_request_kind="approval",
            pending_request_payload={"request_id": "REQ-DECISION", "kind": "file_change"},
        )
        server.agent_runtime.store.create(snapshot)
        server.agent_runtime._snapshots[snapshot.agent_session_id] = snapshot

        with pytest.raises(HoloAuthorizationError, match="Master UI"):
            await server.holo_respond_task_authorized(
                snapshot.agent_session_id,
                "REQ-DECISION",
                "approval",
                {"decision": "approve_once"},
            )
        with pytest.raises(HoloAuthorizationError, match="Master UI"):
            await server.holo_respond_task_authorized(
                snapshot.agent_session_id,
                "REQ-DECISION",
                "plan",
                {"decision": "approve"},
            )

    asyncio.run(scenario())


def test_holo_task_status_does_not_read_history_for_running_session(tmp_path: Path, monkeypatch) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    now = utc_now_iso()
    snapshot = AgentSessionSnapshot(
        task_id="T-STATUS", agent_session_id="AS-STATUS", resident="Lapan", provider="codex",
        working_dir=str(tmp_path), run_state="running", started_at=now, updated_at=now,
        origin_chat_session_id=server.sessions.active_session_id,
    )
    server.agent_runtime._snapshots[snapshot.agent_session_id] = snapshot

    def unexpected_read(*args, **kwargs):
        pytest.fail("Task status must not read event history")

    monkeypatch.setattr(server.agent_runtime.store, "read_event_tail", unexpected_read)
    monkeypatch.setattr(server.agent_runtime.store, "read_events", unexpected_read)
    assert server._task_status("T-STATUS")["state"] == "running"


def test_holo_task_status_keeps_pre_agent_failure_after_world_receives_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)

        class World:
            async def send(self, message):
                pass

        server._world_connection = World()
        await server._send_task_update("T-EARLY-FAIL", "failed", "Provider is unavailable")
        status = server._task_status("T-EARLY-FAIL")
        assert status["terminal"] is True
        assert status["phase"] == "failed"
        assert status["text"] == "Provider is unavailable"

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), float("-inf")])
def test_holo_task_wait_requires_finite_timeout(tmp_path: Path, timeout: float) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server.holo_open_attach_window("DIVE-WAIT")
        server.holo_attach()
        with pytest.raises(ValueError, match="finite"):
            await server.holo_wait_task_authorized("T-WAIT", timeout_sec=timeout)

    asyncio.run(scenario())


def test_holo_task_wait_cancels_children_when_handler_stops(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        started = asyncio.Event()
        closed_started = asyncio.Event()
        cleaned: set[str] = set()

        async def wait_task(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.add("task")

        class World:
            async def wait_closed(self):
                closed_started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned.add("connection")

        monkeypatch.setattr(server, "holo_wait_task_authorized", wait_task)
        handler = asyncio.create_task(server._handle_holo_local_message(World(), {
            "type": "holo_task_wait_request", "payload": {"task_id": "T-WAIT", "timeout_sec": 15},
        }))
        await asyncio.wait_for(asyncio.gather(started.wait(), closed_started.wait()), timeout=1)
        handler.cancel()
        with pytest.raises(asyncio.CancelledError):
            await handler
        assert cleaned == {"task", "connection"}

    asyncio.run(scenario())


def test_holo_snapshot_exposes_only_public_allowlist(tmp_path: Path) -> None:
    server = CoreServer(_make_config(tmp_path), port_override=0)
    session_id = server.sessions.active_session_id
    server.sessions.store.append_entry(session_id, kind="say", sender="master", text="public message")
    server.sessions.store.append_entry(
        session_id, kind="whisper", sender="master", to="Lapan", text="private whisper"
    )

    snapshot = server.holo_snapshot()
    assert snapshot["world_connected"] is False
    assert snapshot["active_session"] == session_id
    assert snapshot["residents"] == [{
        "name": "Lapan",
        "role": "executor",
        "provider": "codex",
        "model": "secret-model",
        "availability": "unknown",
        "usage_budget": None,
        "location": "center",
    }]
    assert snapshot["integrated_audit"]["active"] == []
    assert snapshot["integrated_audit"]["last_completed"] is None
    assert snapshot["integrated_audit"]["cadence_reference_seconds"] == 18_000
    assert [entry["text"] for entry in snapshot["recent_public_entries"]] == ["public message"]
    assert "private whisper" not in str(snapshot)
    assert "lapan/lapan.vrm" not in str(snapshot)
    assert "private persona detail" not in str(snapshot)


def test_holo_event_wait_success_timeout_and_cancel_release_waiters() -> None:
    async def scenario() -> None:
        queue = HoloEventQueue()
        waiter = asyncio.create_task(queue.wait_after(0, timeout_sec=1.0))
        await asyncio.sleep(0)
        assert queue.active_waiters == 1
        published = await queue.publish("world.public_entry", {"text": "hello"})
        result = await waiter
        assert result.events == (published,)
        assert queue.active_waiters == 0

        timeout = await queue.wait_after(result.latest_event_id, timeout_sec=0.01)
        assert timeout.timed_out is True
        assert queue.active_waiters == 0

        cancelled = asyncio.create_task(queue.wait_after(timeout.latest_event_id, timeout_sec=5.0))
        await asyncio.sleep(0)
        assert queue.active_waiters == 1
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert queue.active_waiters == 0
        await queue.publish("world.public_entry", {"text": "late"})
        assert queue.active_waiters == 0

    asyncio.run(scenario())


def test_holo_world_say_chat_commit_runs_off_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
        )
        original_append = server.sessions.append_holo_say

        def slow_append(*args, **kwargs):
            time.sleep(0.2)
            return original_append(*args, **kwargs)

        monkeypatch.setattr(server.sessions, "append_holo_say", slow_append)
        monkeypatch.setattr(server, "_record_public_memory_entry", lambda _entry: None)
        started = asyncio.get_running_loop().time()
        task = asyncio.create_task(server.holo_world_say("off-loop chat commit"))
        await asyncio.sleep(0.02)
        elapsed = asyncio.get_running_loop().time() - started

        assert elapsed < 0.1
        assert task.done() is False
        entry = await task
        assert entry["text"] == "off-loop chat commit"

    asyncio.run(scenario())


def test_core_holo_world_say_is_public_and_keeps_holo_identity(tmp_path: Path) -> None:
    class CaptureWorld:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send(self, raw: str) -> None:
            self.messages.append(parse_message(raw))

    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        world = CaptureWorld()
        server._world_connection = world  # type: ignore[assignment]
        cursor = server.holo_snapshot()["latest_event_id"]
        entry = await server.holo_world_say("  Codex、確認してくりゃれ  ", to="Lapan")
        assert entry["kind"] == "holo_say"
        assert entry["from"] == "Holo"
        assert entry["to"] == "Lapan"
        assert any(
            message["type"] == "chat_append" and message["payload"]["entry"] == entry
            for message in world.messages
        )
        result = await server.holo_wait_events(cursor, timeout_sec=0.1)
        assert result.events[-1]["payload"]["entry"] == entry
        with pytest.raises(ResidentError):
            await server.holo_world_say("hello", to="Unknown")

    asyncio.run(scenario())


def test_core_holo_world_say_speaks_as_the_holo_addon_resident_when_present(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server.resident_service.create("ホロ", "holo-addon")
        entry = await server.holo_world_say("Lapan、確認してほしいのじゃ", to="Lapan")
        assert entry["kind"] == "holo_say"
        # The World avatar and the chat log must agree on the speaker name.
        assert entry["from"] == "ホロ"
        assert entry["to"] == "Lapan"

    asyncio.run(scenario())


def test_core_holo_events_publish_public_say_but_not_private_whisper(tmp_path: Path) -> None:
    class FakeBrain:
        async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
            return BrainResponse(say=f"{mode} reply", actions=(), passed=False)

        async def cancel(self, invocation_id: str) -> bool:
            return True

    async def wait_response_done(websocket, request_id: str) -> None:
        while True:
            message = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1.0))
            if (
                message["type"] == "response_state"
                and message["payload"].get("request_id") == request_id
                and message["payload"].get("active") is False
            ):
                return

    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            brain_driver=FakeBrain(),
            usage_budget=UsageBudgetService({}),
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", world_hello_payload(server._world_secret), "hello-holo"))
                await websocket.recv()
                cursor = server.holo_snapshot()["latest_event_id"]

                await websocket.send(make_message(
                    "master_say", {"text": "public message", "request_id": "REQ-PUBLIC"}
                ))
                await wait_response_done(websocket, "REQ-PUBLIC")
                public_result = await server.holo_wait_events(cursor, timeout_sec=0.1)
                public_texts = [
                    event["payload"]["entry"]["text"]
                    for event in public_result.events
                    if event["type"] == "world.public_entry"
                ]
                assert public_texts == ["public message", "talk reply"]
                cursor = public_result.latest_event_id

                await websocket.send(make_message(
                    "master_whisper",
                    {"text": "private whisper", "request_id": "REQ-PRIVATE", "to": "Lapan"},
                ))
                await wait_response_done(websocket, "REQ-PRIVATE")
                private_result = await server.holo_wait_events(cursor, timeout_sec=0.01)
                assert private_result.timed_out is True
                assert private_result.events == ()
        finally:
            await server.stop()

    asyncio.run(scenario())
