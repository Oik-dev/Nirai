from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.cursor_cli_conversation import CursorCliConversationAdapter
from core.agents.safety import AgentWorkspacePolicy
from core.brains.base import BrainError
from core.brains.native_conversation import NativeConversationBrainService


def _cursor_auth(tmp_path: Path) -> None:
    source = tmp_path / "runtime" / "cursor_profile" / ".cursor"
    source.mkdir(parents=True, exist_ok=True)
    (source / "agent-cli-state.json").write_text('{"opaque":"auth"}\n', encoding="utf-8")


def test_native_conversation_lazily_initializes_cursor_cli_for_resident_continuation(tmp_path: Path) -> None:
    service = NativeConversationBrainService(tmp_path)
    assert "cursor" not in service._adapters

    service._adapter_factories["cursor"] = lambda: CursorCliConversationAdapter(
        tmp_path,
        command_prefix=("cursor-agent",),
    )
    adapter = service._get_adapter("cursor")

    assert isinstance(adapter, CursorCliConversationAdapter)
    assert service._adapters["cursor"] is adapter


def test_cursor_conversation_home_keeps_provider_state_but_scrubs_auth(tmp_path: Path) -> None:
    _cursor_auth(tmp_path)
    adapter = CursorAcpAdapter(AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",)))
    working = tmp_path / "runtime" / "workspace" / "READONLY"
    working.mkdir(parents=True)

    home = adapter._prepare_cursor_home(
        "AS-FIRST",
        working_dir=working,
        stable_key="chat:S-1:public:Lapan",
    )
    provider_state = home / ".cursor" / "provider-session-state.bin"
    provider_state.write_bytes(b"opaque-session-state")
    assert (home / ".cursor" / "agent-cli-state.json").is_file()

    adapter._cleanup_cursor_home_after_turn(home, persistent=True)

    assert home.is_dir()
    assert provider_state.read_bytes() == b"opaque-session-state"
    assert not (home / ".cursor" / "agent-cli-state.json").exists()

    resumed_home = adapter._prepare_cursor_home(
        "AS-SECOND",
        working_dir=working,
        stable_key="chat:S-1:public:Lapan",
    )
    assert resumed_home == home
    assert provider_state.read_bytes() == b"opaque-session-state"
    assert (home / ".cursor" / "agent-cli-state.json").is_file()

    adapter._cleanup_cursor_home_after_turn(home, persistent=True)
    adapter.discard_conversation_context("chat:S-1:public:Lapan")
    assert not home.exists()


def test_native_conversation_reset_resident_removes_only_that_residents_transport_state(
    tmp_path: Path,
) -> None:
    service = NativeConversationBrainService(tmp_path)
    discarded: list[str] = []

    class FakeAdapter:
        provider = "cursor"

        def discard_conversation_context(self, logical_id: str) -> None:
            discarded.append(logical_id)

    service._adapters = {"cursor": FakeAdapter()}  # type: ignore[assignment]
    targets = [
        "chat:S-1:public:Lapan",
        "private:whisper:Lapan",
        "holo:CV-1:resident:Lapan",
    ]
    other = "chat:S-1:public:Other"
    for index, logical_id in enumerate([*targets, other], start=1):
        service.state.save(
            logical_id,
            "cursor",
            f"session-{index}",
            last_seen_entry_id=f"entry-{index}",
        )

    service.reset_resident("Lapan")

    assert [state.logical_id for state in service.state.iter_states()] == [other]
    assert set(discarded) == set(targets)


def test_native_pending_turn_is_not_resumable_until_nirai_commits_seen_marker(tmp_path: Path) -> None:
    service = NativeConversationBrainService(tmp_path)
    logical_id = "chat:S-1:public:Lapan"
    service.state.save(
        logical_id,
        "cursor",
        "session-pending",
        last_seen_entry_id="CE-BEFORE",
        pending_turn=True,
    )

    assert service.has_compatible_state(logical_id, "cursor") is False
    assert service.last_seen_entry_id(logical_id, "cursor") is None

    service.mark_seen(logical_id, "cursor", "CE-AFTER")

    state = service.state.load(logical_id)
    assert state is not None
    assert state.pending_turn is False
    assert state.last_seen_entry_id == "CE-AFTER"
    assert service.has_compatible_state(logical_id, "cursor") is True


def test_native_seen_marker_persist_failure_invalidates_transport_without_failing_local_commit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = NativeConversationBrainService(tmp_path)
    logical_id = "chat:S-SEEN-FAIL:public:Lapan"
    discarded: list[str] = []

    class FakeAdapter:
        provider = "cursor"

        def discard_conversation_context(self, conversation_id: str) -> None:
            discarded.append(conversation_id)

    service._adapters = {"cursor": FakeAdapter()}  # type: ignore[assignment]
    service.state.save(
        logical_id,
        "cursor",
        "session-pending",
        last_seen_entry_id="CE-BEFORE",
        pending_turn=True,
    )
    original_save = service.state.save

    def failing_seen_save(*args, **kwargs):
        if kwargs.get("pending_turn") is False:
            raise BrainError("simulated seen marker persistence failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(service.state, "save", failing_seen_save)
    service.mark_seen(logical_id, "cursor", "CE-AFTER")

    assert service.state.load(logical_id) is None
    assert discarded == [logical_id]


def test_native_context_suppresses_same_world_and_private_memory_until_context_is_reset(tmp_path: Path) -> None:
    service = NativeConversationBrainService(tmp_path)
    logical_id = "private:whisper:Lapan"
    resident = {"name": "Lapan", "persona": "# Lapan"}
    world_memory = {
        "episode_id": "E-1",
        "session_id": "S-OLD",
        "path": "world_memory/episodes/E-1.md",
        "excerpt": "青い貝殻を拾った",
    }
    private_memory = {
        "memory_id": "CE-PRIVATE-OLD",
        "resident": "Lapan",
        "path": "residents/Lapan/private/private_memory.sqlite3#entry:CE-PRIVATE-OLD",
        "excerpt": "秘密の合言葉は月影77",
    }
    context = {
        "history": [],
        "world_memories": [world_memory],
        "private_memories": [private_memory],
        "skills": "skill-a",
    }

    first, static_hash, memory_hashes = service.prepare_prompt_context(
        logical_id,
        "whisper",
        resident,
        context,
    )
    assert first["world_memories"] == [world_memory]
    assert first["private_memories"] == [private_memory]
    assert first["_native_static_refresh"] is True
    service.mark_context_delivered(logical_id, static_hash, memory_hashes)

    second, _static_hash, second_memory_hashes = service.prepare_prompt_context(
        logical_id,
        "whisper",
        resident,
        context,
    )
    assert second["world_memories"] == []
    assert second["private_memories"] == []
    assert second["_native_static_refresh"] is False
    assert second_memory_hashes == set()

    service.reset(logical_id)
    rebuilt, _static_hash, _memory_hashes = service.prepare_prompt_context(
        logical_id,
        "whisper",
        resident,
        context,
    )
    assert rebuilt["world_memories"] == [world_memory]
    assert rebuilt["private_memories"] == [private_memory]
    assert rebuilt["_native_static_refresh"] is True


def test_native_conversation_serializes_same_logical_id_but_allows_independent_ids(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = NativeConversationBrainService(tmp_path)
        active = 0
        peak = 0

        class ConcurrentAdapter:
            provider = "cursor"

            async def run(self, request, *, emit, wait_for_master):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.03)
                await emit("run_state", {"provider_session_id": request.provider_session_id or request.conversation_id})
                active -= 1
                return '{"say":"ok","actions":[],"pass":false,"to":null}'

            async def cancel(self, invocation_id: str) -> bool:
                return False

            def discard_conversation_context(self, conversation_id: str) -> None:
                pass

        service._adapters = {"cursor": ConcurrentAdapter()}  # type: ignore[assignment]
        resident = {"name": "Lapan", "persona": "# Lapan"}
        context = {"history": [], "world_memories": [], "skills": ""}

        await asyncio.gather(
            service.think("INV-SAME-1", "talk", resident, context, provider="cursor", logical_id="same"),
            service.think("INV-SAME-2", "talk", resident, context, provider="cursor", logical_id="same"),
        )
        assert peak == 1

        peak = 0
        await asyncio.gather(
            service.think("INV-A", "talk", resident, context, provider="cursor", logical_id="A"),
            service.think("INV-B", "talk", resident, context, provider="cursor", logical_id="B"),
        )
        assert peak == 2

    asyncio.run(scenario())


def test_native_conversation_accepts_core_owned_logical_lock_without_reacquiring(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = NativeConversationBrainService(tmp_path)

        class CoreOwnedLockAdapter:
            provider = "cursor"

            async def run(self, request, *, emit, wait_for_master):
                await emit("run_state", {"provider_session_id": "session-core-owned"})
                return '{"say":"ok","actions":[],"pass":false,"to":null}'

            async def cancel(self, invocation_id: str) -> bool:
                return False

            def discard_conversation_context(self, conversation_id: str) -> None:
                pass

        service._adapters = {"cursor": CoreOwnedLockAdapter()}  # type: ignore[assignment]
        resident = {"name": "Lapan", "persona": "# Lapan"}
        context = {
            "history": [],
            "world_memories": [],
            "skills": "",
            "_native_lock_held": True,
        }
        logical_id = "chat:S-CORE-LOCK:public:Lapan"
        lock = service.conversation_lock(logical_id)

        async with lock:
            response = await asyncio.wait_for(
                service.think(
                    "INV-CORE-LOCK",
                    "talk",
                    resident,
                    context,
                    provider="cursor",
                    logical_id=logical_id,
                ),
                timeout=0.5,
            )

        assert response.say == "ok"
        state = service.state.load(logical_id)
        assert state is not None
        assert state.pending_turn is True

    asyncio.run(scenario())


def test_native_state_persist_failure_discards_advanced_provider_context(tmp_path: Path, monkeypatch) -> None:
    async def scenario() -> None:
        service = NativeConversationBrainService(tmp_path)
        logical_id = "chat:S-PERSIST-FAIL:public:Lapan"
        discarded: list[str] = []
        resume_ids: list[str | None] = []

        class PersistFailureAdapter:
            provider = "cursor"

            async def run(self, request, *, emit, wait_for_master):
                resume_ids.append(request.provider_session_id)
                await emit("run_state", {"provider_session_id": "session-advanced"})
                return '{"say":"ok","actions":[],"pass":false,"to":null}'

            async def cancel(self, invocation_id: str) -> bool:
                return False

            def discard_conversation_context(self, conversation_id: str) -> None:
                discarded.append(conversation_id)

        service._adapters = {"cursor": PersistFailureAdapter()}  # type: ignore[assignment]
        original_save = service.state.save

        def failing_save(*args, **kwargs):
            if kwargs.get("pending_turn") is True:
                raise BrainError("simulated state persistence failure")
            return original_save(*args, **kwargs)

        monkeypatch.setattr(service.state, "save", failing_save)
        resident = {"name": "Lapan", "persona": "# Lapan"}
        context = {"history": [], "world_memories": [], "skills": ""}

        with pytest.raises(BrainError, match="simulated state persistence failure"):
            await service.think(
                "INV-PERSIST-FAIL",
                "talk",
                resident,
                context,
                provider="cursor",
                logical_id=logical_id,
            )

        assert logical_id in discarded
        assert service.state.load(logical_id) is None
        assert logical_id not in service._static_context_hashes
        assert logical_id not in service._injected_memory_hashes

        monkeypatch.setattr(service.state, "save", original_save)
        response = await service.think(
            "INV-PERSIST-RETRY",
            "talk",
            resident,
            context,
            provider="cursor",
            logical_id=logical_id,
        )
        assert response.say == "ok"
        assert resume_ids == [None, None]

    asyncio.run(scenario())


def test_codex_compaction_keeps_thread_identity_but_invalidates_context_delivery_cache(
    tmp_path: Path,
) -> None:
    service = NativeConversationBrainService(tmp_path)
    logical_id = "private:whisper:Lapan"
    service.state.save(
        logical_id,
        "codex",
        "thread-1",
        last_seen_entry_id="CE-1",
    )
    resident = {"name": "Lapan", "persona": "# Lapan"}
    memory = {
        "episode_id": "E-1",
        "session_id": "S-OLD",
        "path": "world_memory/episodes/E-1.md",
        "excerpt": "青い貝殻を拾った",
    }
    context = {
        "history": [{"from": "master", "text": "覚えてる？"}],
        "world_memories": [memory],
        "skills": "skill-a",
        "_native_history_delta": True,
        "_native_context_bootstrap": False,
    }
    prepared, static_hash, memory_hashes = service.prepare_prompt_context(
        logical_id,
        "whisper",
        resident,
        context,
    )
    assert prepared["world_memories"] == [memory]
    service.mark_context_delivered(logical_id, static_hash, memory_hashes)

    class CompactingAdapter:
        provider = "codex"

        async def run(self, request, *, emit, wait_for_master):
            assert request.provider_session_id == "thread-1"
            await emit("run_state", {"provider_session_id": "thread-1"})
            await emit("run_state", {"state": "running", "context_compacted": True})
            return '{"say":"覚えているよ","actions":[],"pass":false,"to":null}'

        async def cancel(self, invocation_id: str) -> bool:
            return False

        def discard_conversation_context(self, conversation_id: str) -> None:
            pass

    service._adapters = {"codex": CompactingAdapter()}  # type: ignore[assignment]

    response = asyncio.run(
        service.think(
            "INV-COMPACT",
            "whisper",
            resident,
            context,
            provider="codex",
            logical_id=logical_id,
        )
    )

    assert response.say == "覚えているよ"
    state = service.state.load(logical_id)
    assert state is not None
    assert state.provider_session_id == "thread-1"
    assert logical_id not in service._static_context_hashes
    assert logical_id not in service._injected_memory_hashes

    after_compaction, _static_hash, _memory_hashes = service.prepare_prompt_context(
        logical_id,
        "whisper",
        resident,
        context,
    )
    assert after_compaction["_native_static_refresh"] is True
    assert after_compaction["world_memories"] == [memory]
