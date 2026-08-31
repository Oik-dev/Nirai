import asyncio
import json
import os
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from core.brains.base import BrainResponse
from core.config import load_config
from core.holo import HoloAuthorization, HoloAuthorizationError, HoloDiveBinding, HoloEventQueue
from core.protocol import make_message, parse_message
from core.residents.service import ResidentError
from core.server import CoreServer


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
    binding = authorization.attach()
    assert binding.dive_session_id == "DIVE-1"
    assert authorization.pending_dive_session_id is None
    assert authorization.require_attached() == binding

    with pytest.raises(HoloAuthorizationError):
        authorization.attach()

    authorization.open_attach_window("DIVE-2", ttl_sec=1)
    now[0] = 1002.0
    with pytest.raises(HoloAuthorizationError):
        authorization.attach()


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
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}, "hello-dive"))
                assert parse_message(await websocket.recv())["type"] == "hello_ack"
                await websocket.send(make_message(
                    "holo_dive_started",
                    {"dive_session_id": "DIVE-FROM-UI"},
                ))
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
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret=secret)
        server.holo_open_attach_window("DIVE-CLIENT")
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
            nirai_root = Path(__file__).resolve().parents[2]

            attached = await run_client(nirai_root, env, "attach")
            assert attached["result"]["dive_session_id"] == "DIVE-CLIENT"
            assert secret not in json.dumps(attached)

            before = await run_client(nirai_root, env, "snapshot")
            cursor = before["result"]["snapshot"]["latest_event_id"]
            assert secret not in json.dumps(before)

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
        finally:
            await server.stop()

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
    assert snapshot["residents"] == [{"name": "Lapan", "location": "center"}]
    assert [entry["text"] for entry in snapshot["recent_public_entries"]] == ["public message"]
    assert "private whisper" not in str(snapshot)
    assert "secret-model" not in str(snapshot)
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
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=FakeBrain())
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}, "hello-holo"))
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
