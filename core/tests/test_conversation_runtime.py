from pathlib import Path

import pytest

from core.conversation import ConversationRuntimeError, ConversationStore


def test_conversation_store_persists_history_across_provider_turns_and_restart(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
        target_name="Nirai",
    )

    conversation = store.start_turn(
        conversation,
        holo_sender="Holo",
        text="この仕様をどう思う？",
    )
    first_turn_id = conversation.active_turn_id
    conversation = store.finish_turn(
        conversation,
        sender="cursor",
        text="境界をConversation側に置くのがよいです。",
        agent_session_id="AS-FIRST",
        provider_session_id="cursor-native-1",
    )

    assert conversation.turn_state == "completed"
    assert conversation.last_turn_id == first_turn_id
    assert conversation.last_agent_session_id == "AS-FIRST"
    assert conversation.provider_session_id == "cursor-native-1"
    assert [message.text for message in conversation.messages] == [
        "この仕様をどう思う？",
        "境界をConversation側に置くのがよいです。",
    ]

    restarted = ConversationStore(tmp_path)
    restored = restarted.load(conversation.conversation_id)
    assert restored.participant == "cursor"
    assert restored.mode == "consult"
    assert restored.target_name == "Nirai"
    assert restored.provider_session_id == "cursor-native-1"
    assert [message.text for message in restored.messages] == [
        "この仕様をどう思う？",
        "境界をConversation側に置くのがよいです。",
    ]

    restored = restarted.start_turn(
        restored,
        holo_sender="Holo",
        text="その境界でレビューも共通化できる？",
    )
    assert restored.turn_state == "running"
    assert restored.active_turn_id != first_turn_id
    assert len(restored.messages) == 3


def test_conversation_store_keeps_full_append_only_journal_beyond_hot_tail(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
    )

    for index in range(130):
        conversation = store.append_message(
            conversation,
            role="holo" if index % 2 == 0 else "participant",
            sender="Holo" if index % 2 == 0 else "cursor",
            text=f"message-{index + 1}",
        )

    assert len(conversation.messages) == 100
    assert conversation.messages[0].seq == 31
    full = store.full_messages(conversation)
    assert len(full) == 130
    assert full[0].text == "message-1"
    assert full[-1].text == "message-130"

    restarted = ConversationStore(tmp_path)
    restored = restarted.load(conversation.conversation_id)
    restored_full = restarted.full_messages(restored)
    assert [message.seq for message in restored_full] == list(range(1, 131))


def test_conversation_store_recovers_only_inflight_turn_as_interrupted(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    running = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    running = store.start_turn(
        running,
        holo_sender="Holo",
        text="まだ起きてる？",
    )
    running_turn_id = running.active_turn_id

    completed = store.create(
        participant_kind="provider",
        participant="codex",
        mode="brainstorm",
    )
    completed = store.start_turn(
        completed,
        holo_sender="Holo",
        text="案を出して",
    )
    completed = store.finish_turn(
        completed,
        sender="codex",
        text="三案あります。",
    )

    restarted = ConversationStore(tmp_path)
    recovered = restarted.load(running.conversation_id)
    unchanged = restarted.load(completed.conversation_id)

    assert recovered.turn_state == "interrupted"
    assert recovered.last_turn_id == running_turn_id
    assert recovered.active_turn_id is None
    assert "Core restarted" in (recovered.last_error or "")
    assert [message.text for message in recovered.messages] == ["まだ起きてる？"]

    assert unchanged.turn_state == "completed"
    assert [message.text for message in unchanged.messages] == ["案を出して", "三案あります。"]


def test_conversation_store_restart_invalidates_inflight_provider_native_session(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
    )
    conversation = store.start_turn(conversation, holo_sender="Holo", text="first")
    conversation = store.finish_turn(
        conversation,
        sender="cursor",
        text="first reply",
        provider_session_id="cursor-native-before-crash",
    )
    conversation = store.start_turn(conversation, holo_sender="Holo", text="in flight")
    assert conversation.provider_session_id == "cursor-native-before-crash"

    restarted = ConversationStore(tmp_path)
    restored = restarted.load(conversation.conversation_id)

    assert restored.turn_state == "interrupted"
    assert restored.provider_session_id is None
    assert [message.text for message in restarted.full_messages(restored)] == [
        "first",
        "first reply",
        "in flight",
    ]


def test_conversation_store_repairs_uncommitted_start_message_after_crash(tmp_path: Path, monkeypatch) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    original_save = store.save

    def crash_before_running_snapshot(record):
        if record.turn_state == "running":
            raise SystemExit("simulated process death")
        return original_save(record)

    monkeypatch.setattr(store, "save", crash_before_running_snapshot)
    with pytest.raises(SystemExit, match="simulated process death"):
        store.start_turn(conversation, holo_sender="Holo", text="orphan start")

    restarted = ConversationStore(tmp_path)
    restored = restarted.load(conversation.conversation_id)
    assert restored.turn_state == "idle"
    assert restored.messages == ()
    assert restarted.full_messages(restored) == ()
    assert restarted._journal_path(restored.conversation_id).read_bytes() == b""


def test_conversation_store_repairs_uncommitted_reply_then_marks_running_turn_interrupted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
    )
    conversation = store.start_turn(conversation, holo_sender="Holo", text="committed prompt")
    original_save = store.save

    def crash_before_completed_snapshot(record):
        if record.turn_state == "completed":
            raise SystemExit("simulated process death")
        return original_save(record)

    monkeypatch.setattr(store, "save", crash_before_completed_snapshot)
    with pytest.raises(SystemExit, match="simulated process death"):
        store.finish_turn(conversation, sender="cursor", text="orphan reply")

    restarted = ConversationStore(tmp_path)
    restored = restarted.load(conversation.conversation_id)
    assert restored.turn_state == "interrupted"
    assert [message.text for message in restarted.full_messages(restored)] == ["committed prompt"]
    assert "orphan reply" not in restarted._journal_path(restored.conversation_id).read_text(encoding="utf-8")


def test_conversation_store_does_not_close_running_turn_and_rejects_oversized_message(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    conversation = store.start_turn(
        conversation,
        holo_sender="Holo",
        text="話そう",
    )

    with pytest.raises(ConversationRuntimeError, match="cancelled before close"):
        store.close(conversation)

    conversation = store.end_turn(conversation, "cancelled", error="test cancellation")
    closed = store.close(conversation)
    assert closed.state == "closed"

    another = store.create(
        participant_kind="provider",
        participant="cursor",
        mode="consult",
    )
    with pytest.raises(ConversationRuntimeError, match="exceeds"):
        store.start_turn(
            another,
            holo_sender="Holo",
            text="x" * 32_001,
        )


def test_conversation_store_rejects_stale_start_after_running_turn_committed(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    idle = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    stale_idle = idle
    store.start_turn(idle, holo_sender="Holo", text="first")
    with pytest.raises(ConversationRuntimeError, match="already has a running turn"):
        store.start_turn(stale_idle, holo_sender="Holo", text="second")


def test_conversation_store_rejects_stale_close_after_running_turn_committed(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    idle = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    stale_idle = idle
    store.start_turn(idle, holo_sender="Holo", text="first")
    with pytest.raises(ConversationRuntimeError, match="cancelled before close"):
        store.close(stale_idle)
    latest = store.load(idle.conversation_id)
    assert latest.turn_state == "running"
    assert latest.state == "open"


def test_conversation_store_tracks_open_public_session_bind_until_close(tmp_path: Path) -> None:
    store = ConversationStore(tmp_path)
    conversation = store.create(
        participant_kind="resident",
        participant="Serina",
        mode="talk",
    )
    conversation = store.bind_public_session(conversation, "S-BOUND")
    assert store.has_open_public_session("S-BOUND") is True
    store.close(conversation)
    assert store.has_open_public_session("S-BOUND") is False
