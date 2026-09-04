from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from core.agents import AgentSessionSnapshot
from core.agents.types import utc_now_iso
from core.brains.base import BrainResponse
from core.config import load_config
from core.protocol import make_message, parse_message
from core.server import CoreServer


class InteractiveAgent:
    provider = "codex"

    def __init__(self) -> None:
        self.cancelled: list[str] = []

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {
            "state": "running",
            "provider_session_id": "thread-protocol",
            "provider_turn_id": "turn-protocol",
        })
        await emit("approval_request", {
            "request_id": "approve-protocol",
            "kind": "command",
            "title": "Run tests",
            "description": "python -m pytest",
            "options": ["approve_once", "approve_session", "reject", "cancel"],
        })
        approval = await wait_for_master("approve-protocol", "approval", {})
        assert approval == {"decision": "approve_once"}

        await emit("question_request", {
            "request_id": "question-protocol",
            "title": "Confirmation",
            "questions": [{"id": "q1", "question": "Continue?", "options": []}],
        })
        answer = await wait_for_master("question-protocol", "question", {})
        assert answer == {"answers": {"q1": ["yes"]}}

        await emit("command_execution", {
            "command": "python -m pytest",
            "cwd": str(request.working_dir),
            "status": "completed",
            "exit_code": 0,
            "output": "1 passed",
        })
        await emit("file_change", {
            "status": "completed",
            "changes": [{"relative_path": "result.txt", "diff": "+done"}],
        })
        await emit("diff", {"diff": "+done"})
        await emit("plan", {
            "explanation": "done",
            "steps": [{"step": "test", "status": "completed"}],
        })
        await emit("todo_update", {
            "steps": [{"step": "test", "status": "completed"}],
        })
        return "protocol done"

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        return True


class VolunteerConsultBrain:
    async def think(self, invocation_id, mode, resident, context):
        assert mode == "consult"
        return BrainResponse(
            say="",
            actions=(),
            passed=False,
            volunteer=context.get("can_agent_work") is True,
        )

    async def cancel(self, invocation_id: str) -> bool:
        return True


class ScriptedConsultBrain:
    def __init__(self, volunteers: set[str]) -> None:
        self.volunteers = volunteers
        self.calls: list[tuple[str, bool]] = []
        self.contexts: list[dict[str, Any]] = []

    async def think(self, invocation_id, mode, resident, context):
        assert mode == "consult"
        name = resident["name"]
        can_agent_work = context.get("can_agent_work") is True
        self.calls.append((name, can_agent_work))
        self.contexts.append(dict(context))
        return BrainResponse(
            say=f"{name} opinion",
            actions=(),
            passed=False,
            volunteer=name in self.volunteers,
        )

    async def cancel(self, invocation_id: str) -> bool:
        return True


class BlockingConsultBrain:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def think(self, invocation_id, mode, resident, context):
        assert mode == "consult"
        self.started.set()
        await self.release.wait()
        return BrainResponse(say="no volunteer", actions=(), passed=False, volunteer=False)

    async def cancel(self, invocation_id: str) -> bool:
        self.release.set()
        return True


class HangingCancelConsultBrain(BlockingConsultBrain):
    async def cancel(self, invocation_id: str) -> bool:
        await asyncio.Event().wait()
        return True


class RaisingCancelConsultBrain(BlockingConsultBrain):
    async def cancel(self, invocation_id: str) -> bool:
        raise OSError("cancel transport failed")


class ActionAckWebSocket:
    def __init__(self, server: CoreServer) -> None:
        self.server = server
        self.messages: list[dict[str, Any]] = []

    async def send(self, raw: str) -> None:
        message = parse_message(raw)
        self.messages.append(message)
        if message["type"] != "action":
            return
        action_id = message.get("id")
        if not isinstance(action_id, str):
            return
        waiter = self.server._action_waiters.get(action_id)
        if waiter is not None and not waiter.done():
            waiter.set_result({"ok": True})


class ReleaseCompletingAgent:
    provider = "codex"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, request, *, emit, wait_for_master):
        await emit("run_state", {"state": "running"})
        self.started.set()
        await self.release.wait()
        return "offline completion"

    async def cancel(self, agent_session_id: str) -> bool:
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
enabled = ["Codex"]

[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip(),
        encoding="utf-8",
    )
    resident_dir = tmp_path / "residents" / "Codex"
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "persona.md").write_text("# Codex\n", encoding="utf-8")
    (resident_dir / "config.toml").write_text(
        'brain = "codex"\n',
        encoding="utf-8",
    )
    return load_config(tmp_path)


async def _receive_until(websocket, predicate, *, limit: int = 40) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    for _ in range(limit):
        raw = await asyncio.wait_for(websocket.recv(), timeout=3.0)
        message = parse_message(raw)
        seen.append(message)
        if predicate(message):
            return message, seen
    raise AssertionError(f"target message not received: {seen}")


def test_unauthenticated_world_cannot_replace_authenticated_world_or_receive_snapshot(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0, world_secret="world-secret")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"

            async with connect(uri) as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-real",
                ))
                assert parse_message(await world.recv())["type"] == "hello_ack"

                async with connect(uri) as attacker:
                    await attacker.send(make_message(
                        "hello",
                        {"role": "world", "secret": "wrong-secret"},
                        "hello-fake",
                    ))
                    try:
                        await attacker.recv()
                    except ConnectionClosedError as exc:
                        assert exc.rcvd is not None
                        assert exc.rcvd.code == 4003
                    else:
                        raise AssertionError("unauthenticated World received data")

                await world.send(make_message(
                    "chat_session_list_request",
                    {},
                    "real-world-still-active",
                ))
                response = parse_message(await world.recv())
                assert response["type"] == "chat_session_list"
                assert response["id"] == "real-world-still-active"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_task_consultation_ignores_ineligible_volunteer_and_uses_first_eligible_resident(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedConsultBrain({"Cursor", "Codex2"})
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            brain_driver=brain,
        )
        server.resident_service.create("Cursor", "cursor")
        server.resident_service.create("Codex2", "codex")
        server._provider_is_available = lambda provider: True  # type: ignore[method-assign]

        selected, participants = await server._consult_task_residents(
            "TASK-CONSULT-SELECT",
            "pick a worker",
            server.sessions.active_session_id,
        )

        assert participants == ("Codex", "Cursor", "Codex2")
        assert brain.calls == [
            ("Codex", True),
            ("Cursor", False),
            ("Codex2", True),
        ]
        assert brain.contexts[0]["consult_history"] == []
        assert brain.contexts[1]["consult_history"] == [{
            "resident": "Codex",
            "say": "Codex opinion",
            "volunteer": False,
            "can_agent_work": True,
        }]
        assert [item["resident"] for item in brain.contexts[2]["consult_history"]] == [
            "Codex",
            "Cursor",
        ]
        assert brain.contexts[2]["consult_history"][1]["volunteer"] is False
        assert selected is not None
        assert selected.name == "Codex2"

    asyncio.run(scenario())


def test_task_consultation_three_residents_face_each_current_speaker(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedConsultBrain(set())
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            brain_driver=brain,
        )
        server.resident_service.create("Cursor", "cursor")
        server.resident_service.create("Codex2", "codex")
        server._provider_is_available = lambda provider: True  # type: ignore[method-assign]
        websocket = ActionAckWebSocket(server)
        server._world_connection = websocket  # type: ignore[assignment]

        selected, participants = await server._consult_task_residents(
            "TASK-CONSULT-FACE",
            "discuss visibly",
            server.sessions.active_session_id,
        )

        assert selected is None
        assert participants == ("Codex", "Cursor", "Codex2")
        actions = [message for message in websocket.messages if message["type"] == "action"]
        assert actions[0]["payload"] == {
            "name": "Codex",
            "command": "gather",
            "args": {"participants": ["Codex", "Cursor", "Codex2"]},
        }
        face_pairs = {
            (message["payload"]["name"], message["payload"]["args"]["target"])
            for message in actions
            if message["payload"]["command"] == "face"
        }
        assert face_pairs == {
            ("Cursor", "Codex"),
            ("Codex2", "Codex"),
            ("Codex", "Cursor"),
            ("Codex2", "Cursor"),
            ("Codex", "Codex2"),
            ("Cursor", "Codex2"),
        }

    asyncio.run(scenario())


def test_task_flow_with_only_ineligible_volunteer_stops_without_agent_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedConsultBrain({"Cursor"})
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            brain_driver=brain,
        )
        server.resident_service.create("Cursor", "cursor")
        server._provider_is_available = lambda provider: True  # type: ignore[method-assign]

        await server._run_task_flow("TASK-NO-ELIGIBLE-VOLUNTEER", "do work", None)

        assert server.agent_runtime.list_snapshots() == []
        task_md = tmp_path / "runtime" / "workspace" / "TASK-NO-ELIGIBLE-VOLUNTEER" / "task.md"
        assert task_md.read_text(encoding="utf-8") == "do work\n"

    asyncio.run(scenario())


def test_task_flow_registration_precedes_consulting_publish_so_core_stop_cancels_it(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        consulting_publish_started = asyncio.Event()

        async def block_consulting_publish(task_id, phase, text, **kwargs):
            assert phase == "consulting"
            consulting_publish_started.set()
            await asyncio.Event().wait()

        server._send_task_update = block_consulting_publish  # type: ignore[method-assign]
        await server.start()
        port = server.bound_port
        assert port is not None
        async with connect(f"ws://127.0.0.1:{port}") as world:
            await world.send(make_message(
                "hello",
                {"role": "world", "secret": "world-secret"},
                "hello-start-reservation",
            ))
            assert parse_message(await world.recv())["type"] == "hello_ack"
            await world.send(make_message(
                "task_request",
                {"text": "must be tracked before publish"},
                "task-start-reservation",
            ))
            await asyncio.wait_for(consulting_publish_started.wait(), timeout=0.5)
            assert server._task_flow_task is not None
            await asyncio.wait_for(server.stop(), timeout=0.5)
            assert brain.started.is_set() is False
            assert server._task_flow_task is None or server._task_flow_task.done()

    asyncio.run(scenario())


def test_second_world_task_is_busy_before_first_consulting_publish_finishes(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        consulting_publish_started = asyncio.Event()

        async def block_consulting_publish(task_id, phase, text, **kwargs):
            assert phase == "consulting"
            consulting_publish_started.set()
            await asyncio.Event().wait()

        server._send_task_update = block_consulting_publish  # type: ignore[method-assign]
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"
            async with connect(uri) as first_world:
                await first_world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-first-start-window",
                ))
                assert parse_message(await first_world.recv())["type"] == "hello_ack"
                await first_world.send(make_message(
                    "task_request",
                    {"text": "first task"},
                    "first-start-window",
                ))
                await asyncio.wait_for(consulting_publish_started.wait(), timeout=0.5)

                async with connect(uri) as second_world:
                    await second_world.send(make_message(
                        "hello",
                        {"role": "world", "secret": "world-secret"},
                        "hello-second-start-window",
                    ))
                    assert parse_message(await second_world.recv())["type"] == "hello_ack"
                    await second_world.send(make_message(
                        "task_request",
                        {"text": "second task"},
                        "second-start-window",
                    ))
                    notice, _ = await _receive_until(
                        second_world,
                        lambda message: message["type"] == "notice"
                        and message.get("id") == "second-start-window",
                    )
                    assert "Queue" in notice["payload"]["text"]
                    assert brain.started.is_set() is False
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_task_consult_cancel_timeout_does_not_block_task_flow_shutdown(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        monkeypatch.setattr("core.server.TASK_CONSULT_CANCEL_TIMEOUT_SEC", 0.02)
        brain = HangingCancelConsultBrain()
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        task = asyncio.create_task(server._run_task_flow(
            "TASK-CANCEL-TIMEOUT",
            "hang on cancel",
            None,
            server.sessions.active_session_id,
        ))
        server._task_flow_task = task
        server._task_flow_origin_session_id = server.sessions.active_session_id
        task.add_done_callback(server._task_flow_done)
        await asyncio.wait_for(brain.started.wait(), timeout=0.5)

        await asyncio.wait_for(server._cancel_task_flow(), timeout=0.2)
        assert task.done()
        await asyncio.sleep(0)
        assert server._task_consult_invocations == set()

    asyncio.run(scenario())


def test_task_consult_cancel_os_error_does_not_skip_task_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = RaisingCancelConsultBrain()
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        task = asyncio.create_task(server._run_task_flow(
            "TASK-CANCEL-ERROR",
            "raise on cancel",
            None,
            server.sessions.active_session_id,
        ))
        server._task_flow_task = task
        server._task_flow_origin_session_id = server.sessions.active_session_id
        task.add_done_callback(server._task_flow_done)
        await asyncio.wait_for(brain.started.wait(), timeout=0.5)

        await asyncio.wait_for(server._cancel_task_flow(), timeout=0.2)
        assert task.done()
        await asyncio.sleep(0)
        assert server._task_consult_invocations == set()

    asyncio.run(scenario())


def test_task_consultation_blocks_origin_session_and_resident_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-mutation-guard",
                ))
                hello = parse_message(await world.recv())
                origin_session_id = hello["payload"]["active_session"]

                await world.send(make_message(
                    "task_request",
                    {"text": "hold consultation"},
                    "task-mutation-guard",
                ))
                await _receive_until(
                    world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "consulting",
                )
                await asyncio.wait_for(brain.started.wait(), timeout=0.5)

                requests = [
                    ("chat_session_delete", {"session_id": origin_session_id}, "delete-origin"),
                    ("world_memory_forget_session", {"session_id": origin_session_id}, "forget-origin"),
                    ("resident_set_brain", {"name": "Codex", "provider": "cursor"}, "change-resident"),
                    ("resident_delete", {"name": "Codex", "confirm": "Delete"}, "delete-resident"),
                ]
                for message_type, payload, request_id in requests:
                    await world.send(make_message(message_type, payload, request_id))
                    notice, _ = await _receive_until(
                        world,
                        lambda message, expected=request_id: (
                            message["type"] == "notice" and message.get("id") == expected
                        ),
                    )
                    assert "Task相談" in notice["payload"]["text"]

                assert server.sessions.store.has_session(origin_session_id)
                assert server.resident_service.load("Codex").brain == "codex"
                brain.release.set()
                failed, _ = await _receive_until(
                    world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "failed",
                )
                assert "誰も手が挙がらなかった" in failed["payload"]["text"]
        finally:
            brain.release.set()
            await server.stop()

    asyncio.run(scenario())


def test_task_consultation_survives_world_replacement_during_formation(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        server.resident_service.create("Cursor", "cursor")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"
            async with connect(uri) as first_world:
                await first_world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-first-formation",
                ))
                assert parse_message(await first_world.recv())["type"] == "hello_ack"
                await first_world.send(make_message(
                    "task_request",
                    {"text": "survive replacement"},
                    "task-world-replace",
                ))
                await _receive_until(
                    first_world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "consulting",
                )
                action, _ = await _receive_until(
                    first_world,
                    lambda message: message["type"] == "action"
                    and message["payload"].get("command") == "approach",
                )
                assert action["payload"]["name"] == "Codex"

                async with connect(uri) as second_world:
                    await second_world.send(make_message(
                        "hello",
                        {"role": "world", "secret": "world-secret"},
                        "hello-second-formation",
                    ))
                    assert parse_message(await second_world.recv())["type"] == "hello_ack"
                    await asyncio.wait_for(brain.started.wait(), timeout=0.5)
                    brain.release.set()
                    failed, _ = await _receive_until(
                        second_world,
                        lambda message: message["type"] == "task_update"
                        and message["payload"].get("phase") == "failed",
                    )
                    assert "誰も手が挙がらなかった" in failed["payload"]["text"]
                    assert server.agent_runtime.list_snapshots() == []
        finally:
            brain.release.set()
            await server.stop()

    asyncio.run(scenario())


def test_task_consultation_world_replacement_during_formation_still_starts_eligible_agent(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=VolunteerConsultBrain(),
        )
        server.resident_service.create("Cursor", "cursor")
        fake = ReleaseCompletingAgent()
        server.agent_runtime._adapters["codex"] = fake
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"
            async with connect(uri) as first_world:
                await first_world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-first-positive-formation",
                ))
                assert parse_message(await first_world.recv())["type"] == "hello_ack"
                await first_world.send(make_message(
                    "task_request",
                    {"text": "survive replacement and start agent"},
                    "task-positive-world-replace",
                ))
                await _receive_until(
                    first_world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "consulting",
                )
                await _receive_until(
                    first_world,
                    lambda message: message["type"] == "action"
                    and message["payload"].get("command") == "approach",
                )

                async with connect(uri) as second_world:
                    await second_world.send(make_message(
                        "hello",
                        {"role": "world", "secret": "world-secret"},
                        "hello-second-positive-formation",
                    ))
                    assert parse_message(await second_world.recv())["type"] == "hello_ack"
                    await asyncio.wait_for(fake.started.wait(), timeout=0.5)
                    active = [
                        snapshot
                        for snapshot in server.agent_runtime.list_snapshots()
                        if snapshot.run_state not in {"completed", "failed", "cancelled", "interrupted"}
                    ]
                    assert len(active) == 1
                    assert active[0].resident == "Codex"
                    fake.release.set()
                    done, _ = await _receive_until(
                        second_world,
                        lambda message: message["type"] == "task_update"
                        and message["payload"].get("phase") == "done",
                    )
                    assert done["payload"]["agent_session_id"] == active[0].agent_session_id
        finally:
            fake.release.set()
            await server.stop()

    asyncio.run(scenario())


def test_task_consultation_survives_world_disconnect_during_formation(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        server.resident_service.create("Cursor", "cursor")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"
            async with connect(uri) as first_world:
                await first_world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-disconnect-formation",
                ))
                assert parse_message(await first_world.recv())["type"] == "hello_ack"
                await first_world.send(make_message(
                    "task_request",
                    {"text": "survive disconnect"},
                    "task-world-disconnect",
                ))
                await _receive_until(
                    first_world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "consulting",
                )
                await _receive_until(
                    first_world,
                    lambda message: message["type"] == "action"
                    and message["payload"].get("command") == "approach",
                )

            await asyncio.wait_for(brain.started.wait(), timeout=0.5)
            brain.release.set()
            for _ in range(100):
                task = server._task_flow_task
                if task is None or task.done():
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Task consultation did not finish while World was disconnected")
            assert any(
                payload.get("phase") == "failed"
                for payload in server._pending_pre_agent_task_updates.values()
            )

            async with connect(uri) as second_world:
                await second_world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-after-formation-disconnect",
                ))
                assert parse_message(await second_world.recv())["type"] == "hello_ack"
                failed, _ = await _receive_until(
                    second_world,
                    lambda message: message["type"] == "task_update"
                    and message["payload"].get("phase") == "failed",
                )
                assert "誰も手が挙がらなかった" in failed["payload"]["text"]
                assert server._pending_pre_agent_task_updates == {}
                assert server.agent_runtime.list_snapshots() == []
        finally:
            brain.release.set()
            await server.stop()

    asyncio.run(scenario())


def test_second_task_request_is_busy_while_first_task_is_still_consulting(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = BlockingConsultBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=brain,
        )
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-consult-busy",
                ))
                assert parse_message(await world.recv())["type"] == "hello_ack"

                await world.send(make_message(
                    "task_request",
                    {"text": "first task"},
                    "first-task",
                ))
                consulting, _ = await _receive_until(
                    world,
                    lambda message: (
                        message["type"] == "task_update"
                        and message.get("id") == "first-task"
                        and message["payload"].get("phase") == "consulting"
                    ),
                )
                assert "agent_session_id" not in consulting["payload"]
                await asyncio.wait_for(brain.started.wait(), timeout=0.5)

                await world.send(make_message(
                    "task_request",
                    {"text": "second task"},
                    "second-task",
                ))
                rejected, _ = await _receive_until(
                    world,
                    lambda message: message["type"] == "notice" and message.get("id") == "second-task",
                )
                assert "Queue" in rejected["payload"]["text"]

                brain.release.set()
                failed, _ = await _receive_until(
                    world,
                    lambda message: (
                        message["type"] == "task_update"
                        and message["payload"].get("phase") == "failed"
                    ),
                )
                assert "誰も手が挙がらなかった" in failed["payload"]["text"]
        finally:
            brain.release.set()
            await server.stop()

    asyncio.run(scenario())


def test_core_restart_recovers_interrupted_agent_result_to_chat_memory_and_world_once(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bootstrap = CoreServer(config, port_override=0)
    origin_session_id = bootstrap.sessions.active_session_id
    now = utc_now_iso()
    stale = AgentSessionSnapshot(
        task_id="TASK-RESTART",
        agent_session_id="AS-RESTART",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-RESTART"),
        run_state="running",
        started_at=now,
        updated_at=now,
        origin_chat_session_id=origin_session_id,
        task_phase="running",
    )
    bootstrap.agent_runtime.store.create(stale)
    bootstrap.agent_runtime.store.append_event(stale, "run_state", {"state": "running"})

    async def scenario() -> None:
        server = CoreServer(config, port_override=0, world_secret="world-secret")
        recovered = server.agent_runtime.snapshot_payload("AS-RESTART")["session"]
        assert recovered["run_state"] == "interrupted"
        assert recovered["origin_chat_session_id"] == origin_session_id
        assert recovered["task_phase"] == "failed"
        assert recovered["result_reported"] is True

        task_entries = [
            entry
            for entry in server.sessions.history(origin_session_id)
            if entry.get("kind") == "task" and entry.get("agent_session_id") == "AS-RESTART"
        ]
        assert len(task_entries) == 1
        assert task_entries[0]["text"].startswith("Task失敗:")
        episode = server.world_memory.episodes_for_session(origin_session_id)[0].read_text(encoding="utf-8")
        assert episode.count("Task失敗:") == 1

        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-recovery",
                ))
                messages = [parse_message(await world.recv()) for _ in range(5)]
                assert [message["type"] for message in messages] == [
                    "hello_ack",
                    "agent_session_snapshot",
                    "chat_append",
                    "task_update",
                    "chat_session_list",
                ]
                assert messages[1]["payload"]["state"] == "interrupted"
                assert messages[2]["payload"]["entry"]["agent_session_id"] == "AS-RESTART"
                assert messages[3]["payload"]["phase"] == "failed"
        finally:
            await server.stop()

        restarted_again = CoreServer(config, port_override=0)
        duplicate_entries = [
            entry
            for entry in restarted_again.sessions.history(origin_session_id)
            if entry.get("kind") == "task" and entry.get("agent_session_id") == "AS-RESTART"
        ]
        assert len(duplicate_entries) == 1
        assert restarted_again._recovered_agent_notifications == {}

    asyncio.run(scenario())


def test_core_restart_repairs_terminal_task_phase_when_result_was_reported_before_crash(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bootstrap = CoreServer(config, port_override=0)
    origin_session_id = bootstrap.sessions.active_session_id
    now = utc_now_iso()
    bootstrap.sessions.append_task(
        origin_session_id,
        "Codex",
        "Task完了: already persisted",
        task_id="TASK-CRASH-PHASE",
        agent_session_id="AS-CRASH-PHASE",
    )
    bootstrap.agent_runtime.store.create(AgentSessionSnapshot(
        task_id="TASK-CRASH-PHASE",
        agent_session_id="AS-CRASH-PHASE",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-CRASH-PHASE"),
        run_state="completed",
        started_at=now,
        updated_at=now,
        origin_chat_session_id=origin_session_id,
        task_phase="running",
        result_reported=True,
        final_summary="already persisted",
    ))

    recovered = CoreServer(config, port_override=0)
    session = recovered.agent_runtime.snapshot_payload("AS-CRASH-PHASE")["session"]
    assert session["run_state"] == "completed"
    assert session["task_phase"] == "done"
    assert session["result_reported"] is True
    assert "AS-CRASH-PHASE" in recovered._recovered_agent_notifications
    entries = [
        entry
        for entry in recovered.sessions.history(origin_session_id)
        if entry.get("agent_session_id") == "AS-CRASH-PHASE"
    ]
    assert len(entries) == 1


def test_core_restart_replays_persisted_terminal_result_when_world_notification_was_not_committed(tmp_path: Path) -> None:
    config = _make_config(tmp_path)
    bootstrap = CoreServer(config, port_override=0)
    origin_session_id = bootstrap.sessions.active_session_id
    now = utc_now_iso()
    bootstrap.sessions.append_task(
        origin_session_id,
        "Codex",
        "Task完了: persisted before crash",
        task_id="TASK-NOTIFY-CRASH",
        agent_session_id="AS-NOTIFY-CRASH",
    )
    bootstrap.agent_runtime.store.create(AgentSessionSnapshot(
        task_id="TASK-NOTIFY-CRASH",
        agent_session_id="AS-NOTIFY-CRASH",
        resident="Codex",
        provider="codex",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-NOTIFY-CRASH"),
        run_state="completed",
        started_at=now,
        updated_at=now,
        origin_chat_session_id=origin_session_id,
        task_phase="done",
        result_reported=True,
        result_notified=False,
        final_summary="persisted before crash",
    ))

    async def scenario() -> None:
        server = CoreServer(config, port_override=0, world_secret="world-secret")
        assert "AS-NOTIFY-CRASH" in server._recovered_agent_notifications
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-notify-recovery",
                ))
                messages = [parse_message(await world.recv()) for _ in range(5)]
                assert [message["type"] for message in messages] == [
                    "hello_ack",
                    "agent_session_snapshot",
                    "chat_append",
                    "task_update",
                    "chat_session_list",
                ]
                assert messages[1]["payload"]["agent_session_id"] == "AS-NOTIFY-CRASH"
                assert messages[3]["payload"]["phase"] == "done"
                assert server.agent_runtime.snapshot_payload("AS-NOTIFY-CRASH")["session"]["result_notified"] is True
        finally:
            await server.stop()

        restarted = CoreServer(config, port_override=0)
        assert restarted._recovered_agent_notifications == {}
        session = restarted.agent_runtime.snapshot_payload("AS-NOTIFY-CRASH")["session"]
        assert session["result_reported"] is True
        assert session["result_notified"] is True

    asyncio.run(scenario())


def test_world_reconnect_replays_terminal_agent_result_without_core_restart(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
            brain_driver=VolunteerConsultBrain(),
        )
        fake = ReleaseCompletingAgent()
        server.agent_runtime._adapters["codex"] = fake
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"

            async with connect(uri) as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-live-offline",
                ))
                assert parse_message(await world.recv())["type"] == "hello_ack"
                await world.send(make_message(
                    "task_request",
                    {"text": "finish while disconnected"},
                    "task-live-offline",
                ))
                assigned, _ = await _receive_until(
                    world,
                    lambda message: (
                        message["type"] == "task_update"
                        and message["payload"].get("phase") == "assigned"
                    ),
                )
                agent_session_id = assigned["payload"]["agent_session_id"]
                assert assigned["payload"]["working_dir"]
                await asyncio.wait_for(fake.started.wait(), timeout=0.5)

            for _ in range(100):
                if server._world_connection is None:
                    break
                await asyncio.sleep(0.01)
            assert server._world_connection is None

            fake.release.set()
            for _ in range(100):
                snapshot = server.agent_runtime.snapshot_payload(agent_session_id)["session"]
                if snapshot["run_state"] == "completed":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("Agent Session did not complete while World was disconnected")

            offline_snapshot = server.agent_runtime.snapshot_payload(agent_session_id)["session"]
            assert offline_snapshot["result_reported"] is True
            assert offline_snapshot["result_notified"] is False
            assert agent_session_id in server._recovered_agent_notifications

            async with connect(uri) as world:
                await world.send(make_message(
                    "hello",
                    {"role": "world", "secret": "world-secret"},
                    "hello-live-reconnect",
                ))
                messages = [parse_message(await world.recv()) for _ in range(5)]
                assert [message["type"] for message in messages] == [
                    "hello_ack",
                    "agent_session_snapshot",
                    "chat_append",
                    "task_update",
                    "chat_session_list",
                ]
                assert messages[1]["payload"]["agent_session_id"] == agent_session_id
                assert messages[3]["payload"]["agent_session_id"] == agent_session_id
                assert messages[3]["payload"]["phase"] == "done"
                assert messages[3]["payload"]["working_dir"] == messages[1]["payload"]["working_dir"]

            restored = server.agent_runtime.snapshot_payload(agent_session_id)["session"]
            assert restored["result_notified"] is True
            assert agent_session_id not in server._recovered_agent_notifications
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_core_stop_rejects_new_agent_task_requests_after_shutdown_begins(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            world_secret="world-secret",
        )
        fake = ReleaseCompletingAgent()
        server.agent_runtime._adapters["codex"] = fake
        shutdown_gate_entered = asyncio.Event()
        release_shutdown = asyncio.Event()

        async def blocked_cancel_all_responses() -> None:
            shutdown_gate_entered.set()
            await release_shutdown.wait()

        server._cancel_all_responses = blocked_cancel_all_responses  # type: ignore[method-assign]
        await server.start()
        port = server.bound_port
        assert port is not None
        uri = f"ws://127.0.0.1:{port}"

        async with connect(uri) as world:
            await world.send(make_message(
                "hello",
                {"role": "world", "secret": "world-secret"},
                "hello-stop-boundary",
            ))
            assert parse_message(await world.recv())["type"] == "hello_ack"

            stop_task = asyncio.create_task(server.stop())
            await asyncio.wait_for(shutdown_gate_entered.wait(), timeout=0.5)

            await world.send(make_message(
                "task_request",
                {"text": "must not start during shutdown"},
                "task-during-stop",
            ))
            notice, _ = await _receive_until(
                world,
                lambda message: message["type"] == "notice" and message.get("id") == "task-during-stop",
            )
            assert "stopping" in notice["payload"]["text"]
            assert fake.started.is_set() is False
            assert server.agent_runtime.list_snapshots() == []

            release_shutdown.set()
            await asyncio.wait_for(stop_task, timeout=1.0)

    asyncio.run(scenario())


def test_agent_protocol_task_approval_question_snapshot_and_reconnect(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            brain_driver=VolunteerConsultBrain(),
        )
        fake = InteractiveAgent()
        server.agent_runtime._adapters["codex"] = fake
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            uri = f"ws://127.0.0.1:{port}"

            async with connect(uri) as websocket:
                await websocket.send(make_message("hello", {"role": "world", "secret": server._world_secret}, "hello-agent"))
                hello = parse_message(await websocket.recv())
                assert hello["type"] == "hello_ack"
                origin_session_id = hello["payload"]["active_session"]

                await websocket.send(make_message("task_request", {"text": "small task"}, "task-req"))
                task_update, task_messages = await _receive_until(
                    websocket,
                    lambda message: (
                        message["type"] == "task_update"
                        and message["payload"].get("phase") == "assigned"
                    ),
                )
                agent_session_id = task_update["payload"]["agent_session_id"]
                assert task_update["id"] == "task-req"
                assert task_update["payload"]["phase"] == "assigned"
                assert task_update["payload"]["assigned_resident"] == "Codex"
                assert task_update["payload"]["assignment_policy"] == "first_eligible_volunteer"
                assert any(
                    message["type"] == "agent_event"
                    and message["payload"]["event"]["type"] == "run_state"
                    for message in task_messages
                )

                approval, _ = await _receive_until(
                    websocket,
                    lambda message: (
                        message["type"] == "agent_event"
                        and message["payload"]["event"]["type"] == "approval_request"
                    ),
                )
                assert approval["payload"]["event"]["agent_session_id"] == agent_session_id

                await websocket.send(make_message("chat_session_delete", {
                    "session_id": origin_session_id,
                }, "delete-active-task"))
                rejected_delete, _ = await _receive_until(
                    websocket,
                    lambda message: message["type"] == "notice" and message.get("id") == "delete-active-task",
                )
                assert "Agent作業中" in rejected_delete["payload"]["text"]

            # World may disappear while an approval is pending. The Agent must
            # stay blocked and the next World connection must reconstruct it.
            async with connect(uri) as websocket:
                await websocket.send(make_message("hello", {"role": "world", "secret": server._world_secret}, "hello-reconnect"))
                hello = parse_message(await websocket.recv())
                assert hello["type"] == "hello_ack"
                restored = parse_message(await websocket.recv())
                assert restored["type"] == "agent_session_snapshot"
                assert restored["payload"]["agent_session_id"] == agent_session_id
                assert restored["payload"]["state"] == "waiting_for_master"
                assert restored["payload"]["pending_input"]["type"] == "approval_request"
                assert restored["payload"]["pending_input"]["request_id"] == "approve-protocol"

                await websocket.send(make_message("agent_approval_response", {
                    "agent_session_id": agent_session_id,
                    "request_id": "approve-protocol",
                    "decision": "approve_once",
                }))
                question, _ = await _receive_until(
                    websocket,
                    lambda message: (
                        message["type"] == "agent_event"
                        and message["payload"]["event"]["type"] == "question_request"
                    ),
                )
                assert question["payload"]["event"]["payload"]["request_id"] == "question-protocol"

                await websocket.send(make_message("agent_question_response", {
                    "agent_session_id": agent_session_id,
                    "request_id": "question-protocol",
                    "answers": {"q1": ["yes"]},
                }))
                completed, completed_messages = await _receive_until(
                    websocket,
                    lambda message: (
                        message["type"] == "agent_event"
                        and message["payload"]["event"]["type"] == "run_state"
                        and message["payload"]["event"]["payload"].get("state") == "completed"
                    ),
                )
                assert completed["payload"]["event"]["agent_session_id"] == agent_session_id
                event_types = {
                    message["payload"]["event"]["type"]
                    for message in completed_messages
                    if message["type"] == "agent_event"
                }
                assert {"command_execution", "file_change", "diff", "plan", "todo_update"} <= event_types

                done_update, done_messages = await _receive_until(
                    websocket,
                    lambda message: (
                        message["type"] == "task_update"
                        and message["payload"].get("agent_session_id") == agent_session_id
                        and message["payload"].get("phase") == "done"
                    ),
                )
                assert done_update["payload"]["text"] == "Task完了: protocol done"
                task_chat = next(
                    message["payload"]["entry"]
                    for message in done_messages
                    if message["type"] == "chat_append"
                    and message["payload"]["entry"].get("kind") == "task"
                )
                assert task_chat["session"] == origin_session_id
                assert task_chat["task_id"] == task_update["payload"]["task_id"]
                assert task_chat["agent_session_id"] == agent_session_id
                assert "python -m pytest" not in task_chat["text"]

                await websocket.send(make_message("agent_session_snapshot_request", {
                    "agent_session_id": agent_session_id,
                }, "snapshot-req"))
                snapshot, _ = await _receive_until(
                    websocket,
                    lambda message: message["type"] == "agent_session_snapshot" and message.get("id") == "snapshot-req",
                )
                assert snapshot["payload"]["state"] == "completed"
                assert snapshot["payload"]["final_summary"] == "protocol done"
                assert snapshot["payload"]["events"]
                assert "pending_input" not in snapshot["payload"]
                assert all("provider_method" not in event for event in snapshot["payload"]["events"])

                task_dir = Path(snapshot["payload"]["working_dir"])
                assert (task_dir / "task.md").read_text(encoding="utf-8") == "small task\n"
                task_entries = [
                    entry for entry in server.sessions.history(origin_session_id)
                    if entry.get("kind") == "task" and entry.get("agent_session_id") == agent_session_id
                ]
                assert len(task_entries) == 1
                episode_path = server.world_memory.episodes_for_session(origin_session_id)[0]
                episode = episode_path.read_text(encoding="utf-8")
                assert episode.count("Task完了: protocol done") == 1
                assert "python -m pytest" not in episode
        finally:
            await server.stop()

    asyncio.run(scenario())
