import asyncio
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from core.brains.base import BrainResponse, BrainUnavailableError
from core.config import load_config
from core.logging_config import configure_core_logging, shutdown_core_logging
from core.protocol import make_message, parse_message
from core.server import CORE_HOST, CoreServer


class FakeBrain:
    def __init__(self, response: BrainResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []
        self.cancelled: list[str] = []

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.calls.append(
            {
                "invocation_id": invocation_id,
                "mode": mode,
                "resident": resident,
                "context": context,
            }
        )
        return self.response

    async def cancel(self, invocation_id: str) -> bool:
        self.cancelled.append(invocation_id)
        return True


class SlowFakeBrain(FakeBrain):
    def __init__(self, response: BrainResponse) -> None:
        super().__init__(response)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.calls.append(
            {
                "invocation_id": invocation_id,
                "mode": mode,
                "resident": resident,
                "context": context,
            }
        )
        self.started.set()
        await self.release.wait()
        return self.response

    async def cancel(self, invocation_id: str) -> bool:
        self.cancelled.append(invocation_id)
        self.release.set()
        return True


class ResidentAwareBrain(FakeBrain):
    def __init__(self) -> None:
        super().__init__(BrainResponse(say="", actions=(), passed=False))
        self.active = 0
        self.max_active = 0

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append(
                {
                    "invocation_id": invocation_id,
                    "mode": mode,
                    "resident": resident,
                    "context": context,
                }
            )
            await asyncio.sleep(0.01)
            return BrainResponse(
                say=f"{resident['name']} reply",
                actions=(),
                passed=False,
            )
        finally:
            self.active -= 1


class ActionAckWebSocket:
    def __init__(self, server: CoreServer) -> None:
        self.server = server
        self.messages: list[dict[str, object]] = []

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
            waiter.set_result({"name": message["payload"].get("name"), "ok": True})


class BlockingActionWebSocket:
    def __init__(self, server: CoreServer) -> None:
        self.server = server
        self.messages: list[dict[str, object]] = []
        self.approach_started = asyncio.Event()

    async def send(self, raw: str) -> None:
        message = parse_message(raw)
        self.messages.append(message)
        if message["type"] != "action":
            return
        action_id = message.get("id")
        if not isinstance(action_id, str):
            return
        command = message["payload"].get("command")
        if command == "approach":
            self.approach_started.set()
            return
        waiter = self.server._action_waiters.get(action_id)
        if waiter is not None and not waiter.done():
            waiter.set_result({"name": message["payload"].get("name"), "ok": True})


class NoStandAckActionWebSocket:
    def __init__(self, server: CoreServer) -> None:
        self.server = server
        self.messages: list[dict[str, object]] = []

    async def send(self, raw: str) -> None:
        message = parse_message(raw)
        self.messages.append(message)
        if message["type"] != "action":
            return
        action_id = message.get("id")
        if not isinstance(action_id, str):
            return
        if message["payload"].get("command") == "stand":
            return
        waiter = self.server._action_waiters.get(action_id)
        if waiter is not None and not waiter.done():
            waiter.set_result({"name": message["payload"].get("name"), "ok": True})


class ScriptedBrain(FakeBrain):
    def __init__(self, responses: list[BrainResponse]) -> None:
        super().__init__(BrainResponse(say="", actions=(), passed=True))
        self.responses = list(responses)

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.calls.append(
            {
                "invocation_id": invocation_id,
                "mode": mode,
                "resident": resident,
                "context": context,
            }
        )
        return self.responses.pop(0)


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
    (resident_dir / "persona.md").write_text("# Lapan\n", encoding="utf-8")
    (resident_dir / "config.toml").write_text(
        'brain = "codex"\navatar = "lapan/lapan.vrm"\n',
        encoding="utf-8",
    )
    return load_config(tmp_path)


def test_server_binds_loopback_and_acknowledges_world(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            assert server.host == CORE_HOST == "127.0.0.1"
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}, "hello-1"))
                response = parse_message(await websocket.recv())
                assert response["type"] == "hello_ack"
                assert response["id"] == "hello-1"
                assert response["payload"]["settings"] == {"audio_volume": 65}
                assert response["payload"]["residents"][0]["name"] == "Lapan"
                assert response["payload"]["residents"][0]["brain"] == "codex"
                assert response["payload"]["residents"][0]["avatar"] == "lapan/lapan.vrm"
                assert response["payload"]["active_session"].startswith("S-")
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_invalid_message_does_not_break_following_hello(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send("not-json")
                await websocket.send(make_message("hello", {"role": "world"}))
                response = parse_message(await websocket.recv())
                assert response["type"] == "hello_ack"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_master_say_echo_persists_and_returns_same_entry(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = FakeBrain(BrainResponse(say="", actions=(), passed=True))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                hello = parse_message(await websocket.recv())
                session_id = hello["payload"]["active_session"]

                await websocket.send(
                    make_message(
                        "master_say",
                        {"text": "Echo test", "request_id": "REQ-ECHO"},
                    )
                )
                echoed = parse_message(await websocket.recv())
                session_list = parse_message(await websocket.recv())
                active = parse_message(await websocket.recv())
                inactive = parse_message(await websocket.recv())

                assert echoed["type"] == "chat_append"
                entry = echoed["payload"]["entry"]
                assert entry["session"] == session_id
                assert entry["text"] == "Echo test"
                assert entry["request_id"] == "REQ-ECHO"
                assert session_list["type"] == "chat_session_list"
                assert active["type"] == "response_state"
                assert active["payload"]["active"] is True
                assert inactive["type"] == "response_state"
                assert inactive["payload"]["active"] is False
                assert server.sessions.history(session_id) == [entry]
                assert brain.calls[0]["mode"] == "talk"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_master_say_brain_transport_failure_sends_warning_before_response_ends(tmp_path: Path) -> None:
    class FailingBrain(FakeBrain):
        async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
            self.calls.append(
                {
                    "invocation_id": invocation_id,
                    "mode": mode,
                    "resident": resident,
                    "context": context,
                }
            )
            raise BrainUnavailableError("Gemini API connection closed before the response body completed")

    async def scenario() -> None:
        brain = FailingBrain(BrainResponse(say="", actions=(), passed=True))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message(
                    "master_say",
                    {"text": "途中切断を確認", "request_id": "REQ-TRANSPORT-FAIL"},
                ))

                messages = [parse_message(await websocket.recv()) for _ in range(5)]
                assert [message["type"] for message in messages] == [
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                    "notice",
                    "response_state",
                ]
                assert messages[2]["payload"]["active"] is True
                assert messages[3]["payload"]["level"] == "WARN"
                assert "connection closed" in messages[3]["payload"]["text"]
                assert messages[4]["payload"]["active"] is False
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_master_say_brain_response_is_persisted_and_returned(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = FakeBrain(BrainResponse(say="こんにちは、Master。", actions=(), passed=False))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                hello = parse_message(await websocket.recv())
                session_id = hello["payload"]["active_session"]

                await websocket.send(
                    make_message(
                        "master_say",
                        {"text": "こんにちは", "request_id": "REQ-BRAIN"},
                    )
                )
                messages = [parse_message(await websocket.recv()) for _ in range(6)]

                assert [message["type"] for message in messages] == [
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                ]
                resident_entry = messages[3]["payload"]["entry"]
                assert resident_entry["kind"] == "resident_say"
                assert resident_entry["from"] == "Lapan"
                assert resident_entry["text"] == "こんにちは、Master。"
                assert resident_entry["request_id"] == "REQ-BRAIN"
                assert resident_entry["session"] == session_id
                assert server.sessions.history(session_id) == [
                    messages[0]["payload"]["entry"],
                    resident_entry,
                ]
                history = brain.calls[0]["context"]
                assert isinstance(history, dict)
                assert history["history"][-1]["text"] == "こんにちは"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_master_say_responds_with_all_enabled_residents_strictly_sequentially(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ResidentAwareBrain()
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message(
                    "master_say",
                    {"text": "みんな返事して", "request_id": "REQ-MULTI"},
                ))

                messages = [parse_message(await websocket.recv()) for _ in range(10)]
                assert [message["type"] for message in messages] == [
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                    "chat_append",
                    "chat_session_list",
                    "chat_append",
                    "chat_session_list",
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                ]
                resident_entries = [messages[index]["payload"]["entry"] for index in (3, 5, 7)]
                assert [entry["from"] for entry in resident_entries] == ["Lapan", "Kina", "Shiro"]
                assert [entry["text"] for entry in resident_entries] == [
                    "Lapan reply",
                    "Kina reply",
                    "Shiro reply",
                ]
                assert [call["resident"]["name"] for call in brain.calls] == ["Lapan", "Kina", "Shiro"]
                assert brain.max_active == 1
                assert messages[2]["payload"]["active"] is True
                assert messages[9]["payload"]["active"] is False
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_chat_alternates_up_to_six_turns_without_private_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ResidentAwareBrain()
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        session_id = server.sessions.active_session_id
        secret = server.sessions.append_master_whisper("Lapan", "PRIVATE-LEAK-SENTINEL", "REQ-SECRET")

        entries = await server.run_resident_chat(
            "Lapan",
            "Kina",
            "今なにしてる？",
            session_id=session_id,
        )

        assert [entry["from"] for entry in entries] == [
            "Lapan",
            "Kina",
            "Lapan",
            "Kina",
            "Lapan",
            "Kina",
        ]
        assert [entry["to"] for entry in entries] == [
            "Kina",
            "Lapan",
            "Kina",
            "Lapan",
            "Kina",
            "Lapan",
        ]
        assert all(entry["kind"] == "resident_chat" for entry in entries)
        assert all("request_id" not in entry for entry in entries)
        assert [call["resident"]["name"] for call in brain.calls] == [
            "Kina",
            "Lapan",
            "Kina",
            "Lapan",
            "Kina",
        ]
        assert all(call["mode"] == "talk" for call in brain.calls)
        assert all(call["context"]["conversation_kind"] == "resident_chat" for call in brain.calls)
        assert all("PRIVATE-LEAK-SENTINEL" not in str(call["context"]) for call in brain.calls)
        assert secret not in server.sessions.public_history(session_id)
        assert brain.max_active == 1

        episode_paths = server.world_memory.episodes_for_session(session_id)
        assert len(episode_paths) == 1
        episode_text = episode_paths[0].read_text(encoding="utf-8")
        assert "今なにしてる？" in episode_text
        assert "PRIVATE-LEAK-SENTINEL" not in episode_text

    asyncio.run(scenario())


def test_resident_chat_choreography_approaches_faces_then_restores_stand(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        websocket = ActionAckWebSocket(server)

        await server.run_resident_chat(
            "Lapan",
            "Kina",
            "少し話そう",
            websocket=websocket,  # type: ignore[arg-type]
        )

        actions = [message for message in websocket.messages if message["type"] == "action"]
        assert [
            (message["payload"]["name"], message["payload"]["command"], message["payload"]["args"])
            for message in actions
        ] == [
            ("Lapan", "approach", {"target": "Kina"}),
            ("Lapan", "face", {"target": "Kina"}),
            ("Kina", "face", {"target": "Lapan"}),
            ("Lapan", "stand", {}),
            ("Kina", "stand", {}),
        ]
        types = [message["type"] for message in websocket.messages]
        assert types[:3] == ["action", "action", "action"]
        assert "chat_append" in types[3:-2]
        assert types[-2:] == ["action", "action"]

    asyncio.run(scenario())


def test_resident_chat_cancel_during_approach_re_raises_and_never_calls_brain(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = FakeBrain(BrainResponse(say="呼ばれない", actions=(), passed=False))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        websocket = BlockingActionWebSocket(server)

        task = asyncio.create_task(server.run_resident_chat(
            "Lapan",
            "Kina",
            "少し話そう",
            websocket=websocket,  # type: ignore[arg-type]
        ))
        await asyncio.wait_for(websocket.approach_started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        commands = [
            message["payload"]["command"]
            for message in websocket.messages
            if message["type"] == "action"
        ]
        assert commands == ["approach", "stand", "stand"]
        assert brain.calls == []
        assert server._action_waiters == {}
        assert server._resident_chat_tasks == set()

    asyncio.run(scenario())


def test_group_resident_chat_cancel_during_brain_does_not_wait_for_unacked_stand(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = SlowFakeBrain(BrainResponse(say="表示しない", actions=(), passed=False))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")
        websocket = NoStandAckActionWebSocket(server)

        task = asyncio.create_task(server.run_group_resident_chat(
            ["Lapan", "Kina", "Shiro"],
            "Lapan",
            "少し話そう",
            websocket=websocket,  # type: ignore[arg-type]
        ))
        await asyncio.wait_for(brain.started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)

        stand_actions = [
            message
            for message in websocket.messages
            if message["type"] == "action" and message["payload"].get("command") == "stand"
        ]
        assert [message["payload"]["name"] for message in stand_actions] == [
            "Lapan",
            "Kina",
            "Shiro",
        ]
        assert server._action_waiters == {}
        assert server._resident_chat_tasks == set()

    asyncio.run(scenario())


def test_resident_chat_stops_after_two_consecutive_passes(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")

        entries = await server.run_resident_chat("Lapan", "Kina", "少し話そう")

        assert len(entries) == 1
        assert entries[0]["from"] == "Lapan"
        assert entries[0]["to"] == "Kina"
        assert [call["resident"]["name"] for call in brain.calls] == ["Kina", "Lapan"]

    asyncio.run(scenario())


def test_resident_chat_pass_counts_even_when_the_final_words_are_not_empty(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="じゃあまたね", actions=(), passed=True),
            BrainResponse(say="うん、またね", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")

        entries = await server.run_resident_chat("Lapan", "Kina", "少し話そう")

        assert [entry["text"] for entry in entries] == ["少し話そう", "じゃあまたね", "うん、またね"]
        assert [call["resident"]["name"] for call in brain.calls] == ["Kina", "Lapan"]

    asyncio.run(scenario())


def test_group_resident_chat_three_participants_can_rejoin_after_pass(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="夕方ならどう？", actions=(), passed=False),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="夕方なら私も行く", actions=(), passed=False),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")
        session_id = server.sessions.active_session_id
        server.sessions.append_master_whisper("Lapan", "GROUP-PRIVATE-SENTINEL", "REQ-GROUP-SECRET")

        entries = await server.run_group_resident_chat(
            ["Lapan", "Kina", "Shiro"],
            "Lapan",
            "海の奥へ行かない？",
            session_id=session_id,
        )

        assert [call["resident"]["name"] for call in brain.calls] == [
            "Kina",
            "Shiro",
            "Lapan",
            "Kina",
            "Shiro",
            "Lapan",
            "Kina",
        ]
        assert [entry["from"] for entry in entries] == ["Lapan", "Shiro", "Kina"]
        assert [entry["text"] for entry in entries] == [
            "海の奥へ行かない？",
            "夕方ならどう？",
            "夕方なら私も行く",
        ]
        assert all("to" not in entry for entry in entries)
        assert all(call["context"]["participants"] == ["Lapan", "Kina", "Shiro"] for call in brain.calls)
        assert all("GROUP-PRIVATE-SENTINEL" not in str(call["context"]) for call in brain.calls)

    asyncio.run(scenario())


def test_group_resident_chat_addressed_to_selects_next_speaker(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="Shiroはどう思う？", actions=(), passed=False, addressed_to="Shiro"),
            BrainResponse(say="私は賛成", actions=(), passed=False),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")

        entries = await server.run_group_resident_chat(
            ["Lapan", "Kina", "Shiro"],
            "Lapan",
            "みんなはどう思う？",
        )

        assert [call["resident"]["name"] for call in brain.calls[:2]] == ["Kina", "Shiro"]
        assert entries[1]["from"] == "Kina"
        assert entries[1]["to"] == "Shiro"
        assert brain.calls[1]["context"]["addressed_to"] == "Shiro"
        assert brain.calls[1]["context"]["previous_speaker"] == "Kina"

    asyncio.run(scenario())


def test_group_resident_chat_choreography_uses_one_gather_action_then_restores_stand(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = ScriptedBrain([
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
            BrainResponse(say="", actions=(), passed=True),
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")
        websocket = ActionAckWebSocket(server)

        await server.run_group_resident_chat(
            ["Lapan", "Kina", "Shiro"],
            "Lapan",
            "少し話そう",
            websocket=websocket,  # type: ignore[arg-type]
        )

        actions = [message for message in websocket.messages if message["type"] == "action"]
        assert [
            (message["payload"]["name"], message["payload"]["command"], message["payload"]["args"])
            for message in actions
        ] == [
            ("Lapan", "gather", {"participants": ["Lapan", "Kina", "Shiro"]}),
            ("Lapan", "stand", {}),
            ("Kina", "stand", {}),
            ("Shiro", "stand", {}),
        ]

    asyncio.run(scenario())


def test_cancel_response_stops_current_resident_and_skips_remaining_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = SlowFakeBrain(BrainResponse(say="表示しない", actions=(), passed=False))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        server.resident_service.create("Kina", "codex")
        server.resident_service.create("Shiro", "codex")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message(
                    "master_say",
                    {"text": "途中で止める", "request_id": "REQ-MULTI-CANCEL"},
                ))
                await websocket.recv()
                await websocket.recv()
                active = parse_message(await websocket.recv())
                assert active["type"] == "response_state"
                assert active["payload"]["active"] is True

                await asyncio.wait_for(brain.started.wait(), timeout=1)
                await websocket.send(make_message(
                    "cancel_response",
                    {"request_id": "REQ-MULTI-CANCEL"},
                ))
                inactive = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1))
                assert inactive["type"] == "response_state"
                assert inactive["payload"]["active"] is False
                assert [call["resident"]["name"] for call in brain.calls] == ["Lapan"]
                assert len(brain.cancelled) == 1
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_cancel_response_stops_only_current_brain_reply_and_next_request_can_run(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = SlowFakeBrain(
            BrainResponse(say="この返答は停止後には表示しない", actions=(), passed=False)
        )
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                hello = parse_message(await websocket.recv())
                session_id = hello["payload"]["active_session"]

                await websocket.send(
                    make_message(
                        "master_say",
                        {"text": "長く考えて", "request_id": "REQ-CANCEL"},
                    )
                )
                master_entry = parse_message(await websocket.recv())
                session_list = parse_message(await websocket.recv())
                active = parse_message(await websocket.recv())
                assert master_entry["type"] == "chat_append"
                assert session_list["type"] == "chat_session_list"
                assert active["type"] == "response_state"
                assert active["payload"] == {
                    "active": True,
                    "request_id": "REQ-CANCEL",
                    "session_id": session_id,
                }

                await asyncio.wait_for(brain.started.wait(), timeout=1)
                invocation_id = str(brain.calls[0]["invocation_id"])
                await websocket.send(
                    make_message("cancel_response", {"request_id": "REQ-CANCEL"})
                )
                inactive = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1))

                assert inactive["type"] == "response_state"
                assert inactive["payload"]["active"] is False
                assert inactive["payload"]["request_id"] == "REQ-CANCEL"
                assert brain.cancelled == [invocation_id]
                assert server.sessions.history(session_id) == [master_entry["payload"]["entry"]]

                await websocket.send(
                    make_message(
                        "master_say",
                        {"text": "次は返して", "request_id": "REQ-NEXT"},
                    )
                )
                messages = [parse_message(await websocket.recv()) for _ in range(6)]
                assert [message["type"] for message in messages] == [
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                ]
                assert messages[3]["payload"]["entry"]["request_id"] == "REQ-NEXT"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_operational_log_keeps_ids_but_not_chat_text(tmp_path: Path) -> None:
    master_text = "THIS-MASTER-TEXT-MUST-NOT-BE-IN-CORE-LOG"
    resident_text = "THIS-RESIDENT-TEXT-MUST-NOT-BE-IN-CORE-LOG"
    brain = FakeBrain(BrainResponse(say=resident_text, actions=(), passed=False))
    config = _make_config(tmp_path)
    configure_core_logging(tmp_path, "INFO")

    async def scenario() -> None:
        server = CoreServer(config, port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(
                    make_message(
                        "master_say",
                        {"text": master_text, "request_id": "REQ-LOG"},
                    )
                )
                for _ in range(6):
                    await websocket.recv()
        finally:
            await server.stop()

    try:
        asyncio.run(scenario())
    finally:
        shutdown_core_logging()

    invocation_id = str(brain.calls[0]["invocation_id"])
    log_paths = list((tmp_path / "runtime" / "logs").glob("core-*.log"))
    assert len(log_paths) == 1
    log_text = log_paths[0].read_text(encoding="utf-8")
    assert "master_say_saved request_id=REQ-LOG" in log_text
    assert f"brain_start request_id=REQ-LOG invocation_id={invocation_id}" in log_text
    assert f"brain_success request_id=REQ-LOG invocation_id={invocation_id}" in log_text
    assert master_text not in log_text
    assert resident_text not in log_text


def test_brain_provider_list_exposes_cursor_when_available_and_keeps_claude_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "core.server.list_cursor_models",
        lambda root: [{"id": "cursor-grok-4.6-high", "display_name": "Grok 4.6 High"}],
    )

    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server._provider_is_available = lambda provider: provider in {"codex", "cursor"}  # type: ignore[method-assign]
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message("brain_provider_list_request", {}, "providers-1"))
                response = parse_message(await websocket.recv())

                assert response["type"] == "brain_provider_list"
                assert response["id"] == "providers-1"
                providers = response["payload"]["providers"]
                codex = next(provider for provider in providers if provider["name"] == "codex")
                claude = next(provider for provider in providers if provider["name"] == "claude-code")
                cursor = next(provider for provider in providers if provider["name"] == "cursor")
                assert codex["available"] is True
                assert codex["display_name"] == "Codex"
                assert claude["available"] is False
                assert cursor["available"] is True
                assert cursor["connected"] is True
                assert cursor["models"] == []

                refreshed_cursor_models = None
                for _ in range(3):
                    refreshed = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1))
                    if refreshed["type"] != "brain_provider_list":
                        continue
                    refreshed_cursor = next(
                        provider
                        for provider in refreshed["payload"]["providers"]
                        if provider["name"] == "cursor"
                    )
                    if refreshed_cursor["models"]:
                        refreshed_cursor_models = refreshed_cursor["models"]
                        break
                assert refreshed_cursor_models == [
                    {"id": "cursor-grok-4.6-high", "display_name": "Grok 4.6 High"}
                ]
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_provider_catalog_refresh_does_not_block_following_websocket_requests(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import threading

    release = threading.Event()

    def slow_cursor_models(root):
        release.wait(timeout=2)
        return [{"id": "slow-model", "display_name": "Slow Model"}]

    monkeypatch.setattr("core.server.list_cursor_models", slow_cursor_models)

    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server._provider_is_available = lambda provider: provider in {"codex", "cursor"}  # type: ignore[method-assign]
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message("brain_provider_list_request", {}, "providers-slow"))
                initial = parse_message(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                assert initial["type"] == "brain_provider_list"
                assert initial["id"] == "providers-slow"

                await websocket.send(make_message("chat_session_list_request", {}, "sessions-during-catalog"))
                sessions = None
                for _ in range(3):
                    candidate = parse_message(await asyncio.wait_for(websocket.recv(), timeout=0.5))
                    if candidate["type"] == "chat_session_list":
                        sessions = candidate
                        break
                assert sessions is not None
                assert sessions["id"] == "sessions-during-catalog"

                release.set()
                cursor_models = None
                for _ in range(3):
                    refreshed = parse_message(await asyncio.wait_for(websocket.recv(), timeout=1))
                    if refreshed["type"] != "brain_provider_list":
                        continue
                    cursor = next(
                        provider
                        for provider in refreshed["payload"]["providers"]
                        if provider["name"] == "cursor"
                    )
                    if cursor["models"]:
                        cursor_models = cursor["models"]
                        break
                assert cursor_models == [{"id": "slow-model", "display_name": "Slow Model"}]
        finally:
            release.set()
            await server.stop()

    asyncio.run(scenario())


def test_brain_driver_cache_is_separate_per_provider(tmp_path: Path, monkeypatch) -> None:
    codex = FakeBrain(BrainResponse(say="codex", actions=(), passed=False))
    cursor = FakeBrain(BrainResponse(say="cursor", actions=(), passed=False))
    monkeypatch.setattr("core.server.CodexDriver", lambda root: codex)
    monkeypatch.setattr("core.server.CursorDriver", lambda root: cursor)

    server = CoreServer(_make_config(tmp_path), port_override=0)

    assert server._get_brain_driver("codex") is codex  # type: ignore[attr-defined]
    assert server._get_brain_driver("cursor") is cursor  # type: ignore[attr-defined]
    assert server._get_brain_driver("codex") is codex  # type: ignore[attr-defined]
    assert server._get_brain_driver("cursor") is cursor  # type: ignore[attr-defined]
    assert len(server._brain_drivers) == 2  # type: ignore[attr-defined]


def test_resident_create_requires_ai_provider(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message("resident_create", {"name": "Kina"}, "resident-no-ai"))
                response = parse_message(await websocket.recv())

                assert response["type"] == "notice"
                assert response["id"] == "resident-no-ai"
                assert "AI選択" in response["payload"]["text"]
                assert not (tmp_path / "residents" / "Kina").exists()
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_create_allows_third_m2_resident_and_rejects_fourth(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server._provider_is_available = lambda provider: provider == "codex"  # type: ignore[method-assign]
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()

                await websocket.send(
                    make_message(
                        "resident_create",
                        {"name": "Kina", "provider": "codex"},
                        "resident-create-2",
                    )
                )
                response = parse_message(await websocket.recv())

                assert response["type"] == "resident_settings_updated"
                assert response["id"] == "resident-create-2"
                resident = response["payload"]["resident"]
                assert resident["name"] == "Kina"
                assert resident["brain"] == "codex"
                assert resident["avatar"] is None
                assert resident["tts"]["style_id"] is None
                assert (tmp_path / "residents" / "Kina" / "persona.md").is_file()
                assert (tmp_path / "residents" / "Kina" / "config.toml").is_file()
                assert server.resident_service.enabled_names == ("Lapan", "Kina")

                await websocket.send(
                    make_message(
                        "resident_create",
                        {"name": "Shiro", "provider": "codex"},
                        "resident-create-3",
                    )
                )
                third_response = parse_message(await websocket.recv())
                assert third_response["type"] == "resident_settings_updated"
                assert third_response["id"] == "resident-create-3"
                assert third_response["payload"]["resident"]["name"] == "Shiro"
                assert server.resident_service.enabled_names == ("Lapan", "Kina", "Shiro")

                await websocket.send(
                    make_message(
                        "resident_create",
                        {"name": "Yuna", "provider": "codex"},
                        "resident-create-blocked-fourth",
                    )
                )
                blocked = parse_message(await websocket.recv())
                assert blocked["type"] == "notice"
                assert blocked["id"] == "resident-create-blocked-fourth"
                assert "3人まで" in blocked["payload"]["text"]
                assert not (tmp_path / "residents" / "Yuna").exists()
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_set_brain_persists_and_returns_updated_settings(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        server._provider_is_available = lambda provider: provider in {"codex", "cursor"}  # type: ignore[method-assign]
        resident_dir = tmp_path / "residents" / "Kina"
        resident_dir.mkdir(parents=True)
        (resident_dir / "persona.md").write_text("# Kina\n", encoding="utf-8")
        (resident_dir / "config.toml").write_text(
            'avatar = "lapan/lapan.vrm"\n',
            encoding="utf-8",
        )
        server.resident_service._enabled_names.append("Kina")  # type: ignore[attr-defined]
        server.resident_service._write_enabled_names()  # type: ignore[attr-defined]
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message(
                    "resident_set_brain",
                    {"name": "Kina", "provider": "cursor"},
                    "brain-change-1",
                ))
                response = parse_message(await websocket.recv())

                assert response["type"] == "resident_settings_updated"
                assert response["id"] == "brain-change-1"
                assert response["payload"]["resident"]["brain"] == "cursor"
                assert server.resident_service.load("Kina").brain == "cursor"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_whisper_is_private_and_does_not_leak_into_following_say_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = FakeBrain(BrainResponse(say="秘密への返事", actions=(), passed=False))
        server = CoreServer(_make_config(tmp_path), port_override=0, brain_driver=brain)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                hello = parse_message(await websocket.recv())
                session_id = hello["payload"]["active_session"]

                await websocket.send(make_message("master_whisper", {
                    "to": "Lapan",
                    "text": "これは誰にも言わない秘密",
                    "request_id": "REQ-WHISPER",
                }))
                whisper_messages = [parse_message(await websocket.recv()) for _ in range(6)]
                assert [message["type"] for message in whisper_messages] == [
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                    "chat_append",
                    "chat_session_list",
                    "response_state",
                ]
                assert whisper_messages[0]["payload"]["entry"]["kind"] == "whisper"
                assert whisper_messages[3]["payload"]["entry"]["kind"] == "resident_whisper"
                assert brain.calls[0]["mode"] == "whisper"
                assert "これは誰にも言わない秘密" in str(brain.calls[0]["context"])

                private_path = tmp_path / "residents" / "Lapan" / "private" / "whispers.jsonl"
                assert private_path.is_file()
                assert "これは誰にも言わない秘密" in private_path.read_text(encoding="utf-8")
                episodes = server.world_memory.episodes_for_session(session_id)
                assert episodes == []

                brain.response = BrainResponse(say="公開の返事", actions=(), passed=False)
                await websocket.send(make_message("master_say", {
                    "text": "公開の話に戻ろう",
                    "request_id": "REQ-PUBLIC-AFTER-WHISPER",
                }))
                for _ in range(6):
                    await websocket.recv()

                assert brain.calls[1]["mode"] == "talk"
                public_context = brain.calls[1]["context"]
                assert "これは誰にも言わない秘密" not in str(public_context)
                assert "秘密への返事" not in str(public_context)
                episode_text = server.world_memory.episodes_for_session(session_id)[0].read_text(encoding="utf-8")
                assert "公開の話に戻ろう" in episode_text
                assert "公開の返事" in episode_text
                assert "秘密" not in episode_text
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_delete_requires_exact_confirmation_and_preserves_world_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        avatar = tmp_path / "avatars" / "lapan" / "lapan.vrm"
        avatar.parent.mkdir(parents=True)
        avatar.write_bytes(b"vrm")
        server.world_memory.record_public_entry({
            "ts": "2026-08-28T12:00:00+09:00",
            "kind": "say",
            "from": "master",
            "text": "共有世界の記憶",
            "session": "S-KEEP",
            "request_id": "REQ-KEEP",
        })
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()

                await websocket.send(make_message("resident_delete", {
                    "name": "Lapan",
                    "confirm": "delete",
                }, "delete-bad"))
                rejected = parse_message(await websocket.recv())
                assert rejected["type"] == "notice"
                assert (tmp_path / "residents" / "Lapan").exists()

                await websocket.send(make_message("resident_delete", {
                    "name": "Lapan",
                    "confirm": "Delete",
                }, "delete-ok"))
                deleted = parse_message(await websocket.recv())
                assert deleted["type"] == "resident_settings_updated"
                assert deleted["payload"] == {"resident": None, "deleted_name": "Lapan"}
                assert not (tmp_path / "residents" / "Lapan").exists()
                assert avatar.exists()
                assert server.world_memory.episodes_for_session("S-KEEP")
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_set_avatar_persists_and_returns_updated_settings(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        avatar = tmp_path / "avatars" / "other" / "other.vrm"
        avatar.parent.mkdir(parents=True)
        avatar.write_bytes(b"vrm")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()

                await websocket.send(
                    make_message(
                        "resident_set_avatar",
                        {"name": "Lapan", "avatar_path": "other/other.vrm"},
                        "resident-avatar-1",
                    )
                )
                response = parse_message(await websocket.recv())

                assert response["type"] == "resident_settings_updated"
                assert response["id"] == "resident-avatar-1"
                assert response["payload"]["resident"]["avatar"] == "other/other.vrm"
                assert server.resident_service.load("Lapan").avatar == "other/other.vrm"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_resident_set_tts_persists_and_returns_updated_settings(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()

                await websocket.send(make_message("resident_set_tts", {
                    "name": "Lapan",
                    "tts": {
                        "enabled": True,
                        "provider": "voicevox",
                        "speaker_uuid": "speaker-1",
                        "style_id": 3,
                        "speed": 1.1,
                        "pitch": 0.05,
                        "intonation": 0.9,
                    },
                }, "resident-tts-1"))
                response = parse_message(await websocket.recv())

                assert response["type"] == "resident_settings_updated"
                assert response["id"] == "resident-tts-1"
                assert response["payload"]["resident"]["tts"]["style_id"] == 3
                assert server.resident_service.load("Lapan").tts.speaker_uuid == "speaker-1"
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_audio_volume_changed_persists_for_next_hello(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()
                await websocket.send(make_message("audio_volume_changed", {"volume": 35}))
                await asyncio.sleep(0.02)

            assert server.audio_volume == 35
            assert load_config(tmp_path).world.audio_volume == 35
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_history_delete_keeps_world_memory_but_forget_deletes_both(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        session_a = server.sessions.active_session_id
        entry_a = server.sessions.append_master_say("履歴だけ消す会話", "REQ-A")
        server.world_memory.record_public_entry(entry_a)
        session_b = server.sessions.create_session()["id"]
        entry_b = server.sessions.append_master_say("記憶と履歴を消す会話", "REQ-B")
        server.world_memory.record_public_entry(entry_b)
        assert server.world_memory.episodes_for_session(session_a)
        assert server.world_memory.episodes_for_session(session_b)

        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                await websocket.recv()

                await websocket.send(make_message(
                    "chat_session_delete",
                    {"session_id": session_a},
                    "delete-history",
                ))
                session_list = parse_message(await websocket.recv())
                history = parse_message(await websocket.recv())
                assert session_list["type"] == "chat_session_list"
                assert history["type"] == "history_response"
                assert not server.sessions.store.has_session(session_a)
                assert server.world_memory.episodes_for_session(session_a)

                await websocket.send(make_message(
                    "world_memory_forget_session",
                    {"session_id": session_b},
                    "forget-memory",
                ))
                session_list = parse_message(await websocket.recv())
                history = parse_message(await websocket.recv())
                assert session_list["type"] == "chat_session_list"
                assert history["type"] == "history_response"
                assert not server.sessions.store.has_session(session_b)
                assert server.world_memory.episodes_for_session(session_b) == []
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_session_protocol_create_list_select_and_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(_make_config(tmp_path), port_override=0)
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            async with connect(f"ws://127.0.0.1:{port}") as websocket:
                await websocket.send(make_message("hello", {"role": "world"}))
                hello = parse_message(await websocket.recv())
                first_id = hello["payload"]["active_session"]

                await websocket.send(make_message("chat_session_list_request", {}))
                session_list = parse_message(await websocket.recv())
                assert session_list["type"] == "chat_session_list"
                assert session_list["payload"]["active_session"] == first_id

                await websocket.send(make_message("chat_session_create", {}))
                created_list = parse_message(await websocket.recv())
                created_history = parse_message(await websocket.recv())
                second_id = created_list["payload"]["active_session"]
                assert second_id != first_id
                assert created_history["type"] == "history_response"
                assert created_history["payload"] == {
                    "session_id": second_id,
                    "entries": [],
                    "next_before": None,
                }

                await websocket.send(
                    make_message("chat_session_select", {"session_id": first_id})
                )
                selected_list = parse_message(await websocket.recv())
                selected_history = parse_message(await websocket.recv())
                assert selected_list["payload"]["active_session"] == first_id
                assert selected_history["payload"] == {
                    "session_id": first_id,
                    "entries": [],
                    "next_before": None,
                }
        finally:
            await server.stop()

    asyncio.run(scenario())
