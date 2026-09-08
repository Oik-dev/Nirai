import asyncio
import json
import os
from pathlib import Path
import threading
import time

import pytest

from core.agents import AgentRuntimeManager
from core.brains.base import BrainResponse
from core.config import load_config
from core.conversation import ConversationRuntimeError
from core.server import CoreServer


class _ConversationFakeBrain:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict, dict]] = []
        self.cancelled: list[str] = []

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.calls.append((invocation_id, mode, resident, context))
        history = context.get("history") if isinstance(context, dict) else []
        latest = history[-1]["text"] if isinstance(history, list) and history else ""
        return BrainResponse(
            say=f"Serina reply: {latest}",
            actions=(),
            passed=False,
        )

    async def cancel(self, invocation_id: str) -> bool:
        self.cancelled.append(invocation_id)
        return True


class _ConversationFakeAdapter:
    provider = "cursor"
    capabilities = frozenset()

    def __init__(
        self,
        summaries: list[str] | None = None,
        *,
        fail_on_calls: set[int] | None = None,
    ) -> None:
        self.summaries = list(summaries or ["相談結果です"])
        self.requests = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled: list[str] = []
        self.discarded_conversation_ids: list[str] = []
        self.fail_on_calls = set(fail_on_calls or ())

    async def run(self, request, *, emit, wait_for_master):
        self.requests.append(request)
        native_session_id = request.provider_session_id or "cursor-native-conversation"
        await emit("run_state", {
            "state": "running",
            "provider_session_id": native_session_id,
        })
        self.started.set()
        call_number = len(self.requests)
        if call_number in self.fail_on_calls:
            raise RuntimeError(f"simulated provider failure on call {call_number}")
        await self.release.wait()
        index = min(call_number - 1, len(self.summaries) - 1)
        return self.summaries[index]

    async def cancel(self, agent_session_id: str) -> bool:
        self.cancelled.append(agent_session_id)
        self.release.set()
        return True

    def discard_conversation_context(self, conversation_id: str) -> None:
        self.discarded_conversation_ids.append(conversation_id)


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
enabled = ["Serina"]

[tasks]
allowed_dirs = ["runtime\\\\workspace"]
""".strip(),
        encoding="utf-8",
    )
    resident_dir = tmp_path / "residents" / "Serina"
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "persona.md").write_text(
        "# Serina\n自然に会話する。\n",
        encoding="utf-8",
    )
    (resident_dir / "config.toml").write_text(
        'brain = "codex"\nspawn_location = "center"\n',
        encoding="utf-8",
    )
    return load_config(tmp_path)


def _attach(server: CoreServer, dive_id: str = "DIVE-CONVERSATION") -> None:
    server.holo_open_attach_window(dive_id)
    server.holo_attach()


class _BlockingConversationBrain:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        self.started.set()
        await self.release.wait()
        return BrainResponse(say="Serina fixed-session reply", actions=(), passed=False)

    async def cancel(self, invocation_id: str) -> bool:
        self.release.set()
        return True


class _NativePendingConversationBrain:
    def __init__(self, server: CoreServer) -> None:
        self.server = server

    async def think(self, invocation_id, mode, resident, context) -> BrainResponse:
        logical_id = context["_native_conversation"]["logical_id"]
        self.server._native_brain.state.save(
            logical_id,
            "codex",
            "native-test-thread",
            last_seen_entry_id=None,
            pending_turn=True,
        )
        return BrainResponse(say="Serina native reply", actions=(), passed=False)

    async def cancel(self, invocation_id: str) -> bool:
        return True


def test_holo_resident_talk_missing_public_session_never_leaves_turn_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
            brain_driver=_ConversationFakeBrain(),
        )
        _attach(server)
        conversation = server.holo_conversation_start_authorized(
            "resident",
            "Serina",
            "talk",
        )
        conversation_id = conversation["conversation_id"]
        monkeypatch.setattr(server.sessions.store, "has_session", lambda _session_id: False)

        with pytest.raises(ConversationRuntimeError, match="public Chat Session is unavailable"):
            await server.holo_conversation_send_authorized(conversation_id, "開始前検証")

        latest = server._conversation_record(conversation_id)
        assert latest.turn_state != "running"
        assert conversation_id not in server._conversation_tasks
        assert conversation_id not in server._conversation_public_sessions

    asyncio.run(scenario())


def test_holo_resident_talk_keeps_public_chat_session_fixed_while_master_switches_tabs(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = _BlockingConversationBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
            brain_driver=brain,
        )
        _attach(server)
        original_session_id = server.sessions.active_session_id
        conversation = server.holo_conversation_start_authorized(
            "resident",
            "Serina",
            "talk",
        )
        conversation_id = conversation["conversation_id"]

        await server.holo_conversation_send_authorized(conversation_id, "元のSessionで話そう")
        await asyncio.wait_for(brain.started.wait(), timeout=0.5)
        assert server._chat_session_has_active_conversation(original_session_id) is True

        second_session_id = server.sessions.create_session()["id"]
        assert server.sessions.active_session_id == second_session_id
        brain.release.set()
        completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert completed["turn_state"] == "completed"

        original_texts = [
            entry["text"]
            for entry in server.sessions.public_history(original_session_id, limit=20)
        ]
        second_texts = [
            entry["text"]
            for entry in server.sessions.public_history(second_session_id, limit=20)
        ]
        assert "元のSessionで話そう" in original_texts
        assert "Serina fixed-session reply" in original_texts
        assert "元のSessionで話そう" not in second_texts
        assert "Serina fixed-session reply" not in second_texts
        assert server._chat_session_has_active_conversation(original_session_id) is False

    asyncio.run(scenario())


def test_holo_resident_native_marker_commits_before_world_publication_await(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
        )
        native_brain = _NativePendingConversationBrain(server)
        server._get_brain_driver = lambda provider: native_brain  # type: ignore[method-assign]
        _attach(server)

        publication_started = asyncio.Event()
        publication_release = asyncio.Event()
        observed_states = []

        async def delayed_publish(resident_name: str, text: str, *, session_id: str) -> None:
            conversation_id = next(iter(server._conversation_public_sessions))
            logical_id = server._holo_resident_brain_conversation_id(conversation_id, resident_name)
            observed_states.append(server._native_brain.state.load(logical_id))
            publication_started.set()
            await publication_release.wait()

        server._publish_resident_conversation_reply = delayed_publish  # type: ignore[method-assign]
        conversation = server.holo_conversation_start_authorized(
            "resident",
            "Serina",
            "talk",
        )
        conversation_id = conversation["conversation_id"]
        await server.holo_conversation_send_authorized(conversation_id, "native commit確認")
        await asyncio.wait_for(publication_started.wait(), timeout=0.5)

        assert len(observed_states) == 1
        state = observed_states[0]
        assert state is not None
        assert state.pending_turn is False
        assert state.last_seen_entry_id == "cvseq:1"
        assert state.last_output_entry_id == "cvseq:2"
        with pytest.raises(ConversationRuntimeError, match="running or finalizing"):
            await server.holo_conversation_send_authorized(conversation_id, "まだpublish中")

        publication_release.set()
        completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert completed["turn_state"] == "completed"

    asyncio.run(scenario())


def test_holo_resident_conversation_uses_same_send_wait_contract_and_keeps_turn_history(tmp_path: Path) -> None:
    async def scenario() -> None:
        brain = _ConversationFakeBrain()
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret="secret",
            brain_driver=brain,
        )
        _attach(server)

        conversation = server.holo_conversation_start_authorized(
            "resident",
            "Serina",
            "talk",
        )
        conversation_id = conversation["conversation_id"]
        assert conversation["turn_state"] == "idle"

        sent = await server.holo_conversation_send_authorized(
            conversation_id,
            "今日はどうしてる？",
        )
        assert sent["turn_state"] == "running"
        completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert completed["turn_state"] == "completed"
        assert [message["text"] for message in completed["messages"]] == [
            "今日はどうしてる？",
            "Serina reply: 今日はどうしてる？",
        ]

        await server.holo_conversation_send_authorized(
            conversation_id,
            "さっきの続きだけど、何してたの？",
        )
        second, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert second["turn_state"] == "completed"
        assert len(brain.calls) == 2
        second_history = brain.calls[1][3]["history"]
        assert [entry["text"] for entry in second_history] == [
            "今日はどうしてる？",
            "Serina reply: 今日はどうしてる？",
            "さっきの続きだけど、何してたの？",
        ]

        closed = server.holo_conversation_close_authorized(conversation_id)
        assert closed["state"] == "closed"

    asyncio.run(scenario())


def test_holo_provider_context_cleanup_is_scheduled_off_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter()
        cleanup_started = threading.Event()

        def slow_discard(conversation_id: str) -> None:
            cleanup_started.set()
            time.sleep(0.2)
            adapter.discarded_conversation_ids.append(conversation_id)

        adapter.discard_conversation_context = slow_discard  # type: ignore[method-assign]
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)
        conversation = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "consult",
            target_name=tmp_path.name,
        )
        conversation_id = conversation["conversation_id"]

        started = time.perf_counter()
        closed = server.holo_conversation_close_authorized(conversation_id)
        elapsed = time.perf_counter() - started
        assert closed["state"] == "closed"
        assert elapsed < 0.1

        for _ in range(100):
            if cleanup_started.is_set():
                break
            await asyncio.sleep(0.002)
        assert cleanup_started.is_set()
        assert server._provider_context_cleanup_tasks
        await asyncio.gather(*tuple(server._provider_context_cleanup_tasks.values()))
        assert conversation_id in adapter.discarded_conversation_ids

    asyncio.run(scenario())


def test_holo_provider_consult_uses_short_lived_read_only_agent_turns_with_nirai_owned_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter([
            "第一回答: Conversationを正本にするのがよいです。",
            "第二回答: 前回の論点を踏まえると、その案でよいです。",
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)

        conversation = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "consult",
            target_name=tmp_path.name,
        )
        conversation_id = conversation["conversation_id"]

        first = await server.holo_conversation_send_authorized(
            conversation_id,
            "Conversation Runtimeの境界をどう置く？",
        )
        first_agent = first["active_agent_session_id"]
        assert isinstance(first_agent, str)
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        assert len(adapter.requests) == 1
        assert adapter.requests[0].read_only is True
        assert adapter.requests[0].purpose == "consult"
        assert adapter.requests[0].working_dir == tmp_path.resolve()

        pending, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=0,
        )
        assert timed_out is True
        assert pending["turn_state"] == "running"

        adapter.release.set()
        completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert completed["turn_state"] == "completed"
        assert completed["active_agent_session_id"] is None
        assert completed["last_agent_session_id"] == first_agent
        assert completed["messages"][-1]["text"].startswith("第一回答")
        assert completed["provider_session_id"] == "cursor-native-conversation"
        assert server.agent_runtime.has_active_session() is False

        adapter.started.clear()
        adapter.release.clear()
        second = await server.holo_conversation_send_authorized(
            conversation_id,
            "レビューも同じ仕組みに載せていい？",
        )
        second_agent = second["active_agent_session_id"]
        assert isinstance(second_agent, str)
        assert second_agent != first_agent
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        second_prompt = adapter.requests[1].prompt
        assert adapter.requests[1].provider_session_id == "cursor-native-conversation"
        assert adapter.requests[1].conversation_id == conversation_id
        assert "第一回答: Conversationを正本にするのがよいです。" not in second_prompt
        assert "Conversation Runtimeの境界をどう置く？" not in second_prompt
        assert "レビューも同じ仕組みに載せていい？" in second_prompt

        adapter.release.set()
        second_completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert second_completed["messages"][-1]["text"].startswith("第二回答")
        assert len(second_completed["messages"]) == 4

    asyncio.run(scenario())


def test_holo_provider_failure_invalidates_native_context_and_rebuilds_from_nirai_journal(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter(
            [
                "first provider reply",
                "unused failed reply",
                "recovered provider reply",
            ],
            fail_on_calls={2},
        )
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)

        conversation = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "consult",
            target_name=tmp_path.name,
        )
        conversation_id = conversation["conversation_id"]

        await server.holo_conversation_send_authorized(conversation_id, "first holo turn")
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        adapter.release.set()
        first, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert first["turn_state"] == "completed"
        assert first["provider_session_id"] == "cursor-native-conversation"

        adapter.started.clear()
        adapter.release.clear()
        await server.holo_conversation_send_authorized(conversation_id, "turn that loses provider context")
        failed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert failed["turn_state"] == "failed"
        assert failed["provider_session_id"] is None
        assert conversation_id in adapter.discarded_conversation_ids

        adapter.fail_on_calls.clear()
        adapter.started.clear()
        adapter.release.clear()
        await server.holo_conversation_send_authorized(conversation_id, "recover from Nirai")
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        recovery_request = adapter.requests[2]
        assert recovery_request.provider_session_id is None
        assert "Nirai recovery transcript" in recovery_request.prompt
        assert "first holo turn" in recovery_request.prompt
        assert "first provider reply" in recovery_request.prompt
        assert "turn that loses provider context" in recovery_request.prompt
        assert "recover from Nirai" in recovery_request.prompt

        adapter.release.set()
        recovered, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )
        assert timed_out is False
        assert recovered["turn_state"] == "completed"
        assert recovered["messages"][-1]["text"] == "recovered provider reply"

    asyncio.run(scenario())


def test_holo_provider_review_is_conversation_mode_with_structured_verdict(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter([
            "NEEDS FIX\n[P1] core/server.py:123: lifecycle mismatch",
        ])
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)

        conversation = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "review",
            target_name=tmp_path.name,
        )
        conversation_id = conversation["conversation_id"]
        await server.holo_conversation_send_authorized(
            conversation_id,
            "現在差分をレビューして",
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
        adapter.release.set()
        completed, timed_out = await server.holo_conversation_wait_authorized(
            conversation_id,
            timeout_sec=1,
        )

        assert timed_out is False
        assert completed["turn_state"] == "completed"
        assert completed["verdict"] == "NEEDS_FIX"
        assert adapter.requests[0].purpose == "review"
        assert adapter.requests[0].read_only is True

    asyncio.run(scenario())


def test_holo_provider_conversation_cancel_does_not_grant_holo_approval_authority(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter()
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)

        conversation = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "brainstorm",
        )
        conversation_id = conversation["conversation_id"]
        sent = await server.holo_conversation_send_authorized(
            conversation_id,
            "実装案を3つ出して",
        )
        agent_session_id = sent["active_agent_session_id"]
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)

        result = await server.holo_conversation_cancel_authorized(conversation_id)
        assert result["cancellation_requested"] is True
        assert result["conversation"]["turn_state"] == "cancelled"
        assert adapter.cancelled == [agent_session_id]
        assert adapter.requests[0].read_only is True
        assert adapter.requests[0].purpose == "brainstorm"

    asyncio.run(scenario())


def test_holo_local_client_uses_one_conversation_contract_for_resident_and_provider(tmp_path: Path) -> None:
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
        secret = "c" * 64
        brain = _ConversationFakeBrain()
        adapter = _ConversationFakeAdapter(["Cursor says: use one durable Conversation contract."])
        server = CoreServer(
            _make_config(tmp_path),
            port_override=0,
            holo_local_secret=secret,
            brain_driver=brain,
        )
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        server.holo_open_attach_window("DIVE-CLIENT-CONVERSATION")
        await server.start()
        try:
            port = server.bound_port
            assert port is not None
            bridge_file = tmp_path / "private" / "holo-local-bridge.json"
            bridge_file.parent.mkdir(parents=True)
            bridge_file.write_text(
                json.dumps({
                    "version": 1,
                    "url": f"ws://127.0.0.1:{port}",
                    "secret": secret,
                    "server_pid": 1234,
                }),
                encoding="utf-8",
            )
            env = {**os.environ, "NIRAI_HOLO_LOCAL_BRIDGE_FILE": str(bridge_file)}
            nirai_root = Path(__file__).resolve().parents[2]

            attached = await run_client(nirai_root, env, "attach")
            assert attached["result"]["dive_session_id"] == "DIVE-CLIENT-CONVERSATION"

            resident_started = await run_client(
                nirai_root,
                env,
                "conversation-start",
                "resident",
                "Serina",
                "talk",
            )
            resident_id = resident_started["result"]["conversation"]["conversation_id"]
            await run_client(
                nirai_root,
                env,
                "conversation-send",
                resident_id,
                "セリナ、今日はどう？",
            )
            resident_done = await run_client(
                nirai_root,
                env,
                "conversation-wait",
                resident_id,
                "1",
            )
            resident_conversation = resident_done["result"]["conversation"]
            assert resident_done["result"]["timed_out"] is False
            assert resident_conversation["turn_state"] == "completed"
            assert resident_conversation["messages"][-1]["sender"] == "Serina"

            provider_started = await run_client(
                nirai_root,
                env,
                "conversation-start",
                "provider",
                "cursor",
                "consult",
                tmp_path.name,
            )
            provider_id = provider_started["result"]["conversation"]["conversation_id"]
            await run_client(
                nirai_root,
                env,
                "conversation-send",
                provider_id,
                "このConversation設計を確認して",
            )
            await asyncio.wait_for(adapter.started.wait(), timeout=0.5)
            adapter.release.set()
            provider_done = await run_client(
                nirai_root,
                env,
                "conversation-wait",
                provider_id,
                "1",
            )
            provider_conversation = provider_done["result"]["conversation"]
            assert provider_done["result"]["timed_out"] is False
            assert provider_conversation["turn_state"] == "completed"
            assert provider_conversation["messages"][-1]["text"].startswith("Cursor says:")
            assert adapter.requests[0].read_only is True
            assert adapter.requests[0].purpose == "consult"

            resident_closed = await run_client(
                nirai_root,
                env,
                "conversation-close",
                resident_id,
            )
            provider_closed = await run_client(
                nirai_root,
                env,
                "conversation-close",
                provider_id,
            )
            assert resident_closed["result"]["conversation"]["state"] == "closed"
            assert provider_closed["result"]["conversation"]["state"] == "closed"
            assert secret not in json.dumps(resident_done)
            assert secret not in json.dumps(provider_done)
        finally:
            await server.stop()

    asyncio.run(scenario())


def test_provider_conversations_can_run_in_parallel_when_they_are_read_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        adapter = _ConversationFakeAdapter(["first", "second"])
        server = CoreServer(_make_config(tmp_path), port_override=0, holo_local_secret="secret")
        server.agent_runtime = AgentRuntimeManager(
            tmp_path,
            server.config.tasks_allowed_dirs,
            adapters={"cursor": adapter},
            broadcast=server._broadcast_agent_event,
        )
        _attach(server)

        first = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "consult",
            target_name=tmp_path.name,
        )
        await server.holo_conversation_send_authorized(
            first["conversation_id"],
            "まずこれを見て",
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=0.5)

        second = server.holo_conversation_start_authorized(
            "provider",
            "cursor",
            "consult",
            target_name=tmp_path.name,
        )
        await server.holo_conversation_send_authorized(
            second["conversation_id"],
            "別の観点でも見て",
        )
        for _ in range(50):
            if len(adapter.requests) == 2:
                break
            await asyncio.sleep(0.01)

        assert len(adapter.requests) == 2
        assert all(request.read_only is True for request in adapter.requests)
        assert adapter.requests[0].working_dir == adapter.requests[1].working_dir == tmp_path.resolve()

        adapter.release.set()
        await server.holo_conversation_wait_authorized(first["conversation_id"], timeout_sec=0.5)
        await server.holo_conversation_wait_authorized(second["conversation_id"], timeout_sec=0.5)

    asyncio.run(scenario())
