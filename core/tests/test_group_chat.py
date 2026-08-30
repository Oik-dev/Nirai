import pytest

from core.conversation import (
    MAX_GROUP_CHAT_PARTICIPANTS,
    GroupConversationError,
    GroupConversationState,
)


def test_group_chat_supports_ten_participants_without_special_case() -> None:
    participants = tuple(f"R{index}" for index in range(MAX_GROUP_CHAT_PARTICIPANTS))
    state = GroupConversationState(participants, "R0")

    assert state.max_turns == 30
    assert state.next_speaker("R0") == "R1"

    speaker = "R1"
    for expected in participants[2:]:
        next_speaker, addressed = state.record_response(
            speaker,
            say="",
            passed=True,
        )
        assert addressed is None
        assert next_speaker == expected
        speaker = expected


def test_group_chat_rejects_more_than_ten_participants() -> None:
    participants = tuple(f"R{index}" for index in range(MAX_GROUP_CHAT_PARTICIPANTS + 1))

    with pytest.raises(GroupConversationError, match="2-10 participants"):
        GroupConversationState(participants, "R0")


def test_group_chat_pass_is_temporary_and_progress_reopens_previous_passers() -> None:
    state = GroupConversationState(("A", "B", "C"), "A")

    assert state.next_speaker("A") == "B"
    next_speaker, _ = state.record_response("B", say="", passed=True)
    assert next_speaker == "C"
    assert state.passed_since_progress == frozenset({"B"})

    next_speaker, _ = state.record_response("C", say="話題を変えよう", passed=False)
    assert state.passed_since_progress == frozenset()
    assert next_speaker == "A"

    next_speaker, _ = state.record_response("A", say="", passed=True)
    assert next_speaker == "B"
    next_speaker, _ = state.record_response("B", say="今の話ならある", passed=False)
    assert state.passed_since_progress == frozenset()
    assert next_speaker == "C"


def test_group_chat_closes_only_after_everyone_passes_since_latest_progress() -> None:
    state = GroupConversationState(("A", "B", "C"), "A")

    next_speaker, _ = state.record_response("B", say="", passed=True)
    assert next_speaker == "C"
    next_speaker, _ = state.record_response("C", say="続けよう", passed=False)
    assert next_speaker == "A"

    next_speaker, _ = state.record_response("A", say="またね", passed=True)
    assert next_speaker == "B"
    next_speaker, _ = state.record_response("B", say="", passed=True)
    assert next_speaker == "C"
    next_speaker, _ = state.record_response("C", say="じゃあね", passed=True)

    assert next_speaker is None
    assert state.finished is True
    assert state.passed_since_progress == frozenset({"A", "B", "C"})


def test_group_chat_addressing_prioritizes_named_participant() -> None:
    state = GroupConversationState(("A", "B", "C", "D"), "A")

    next_speaker, addressed = state.record_response(
        "B",
        say="Dはどう思う？",
        passed=False,
        addressed_to="D",
    )

    assert addressed == "D"
    assert next_speaker == "D"


def test_group_chat_ignores_unknown_or_self_address() -> None:
    state = GroupConversationState(("A", "B", "C"), "A")

    assert state.normalize_address("A", "A") is None
    assert state.normalize_address("A", "Nobody") is None
