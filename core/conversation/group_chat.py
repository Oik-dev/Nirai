from __future__ import annotations

from dataclasses import dataclass, field


MIN_GROUP_CHAT_PARTICIPANTS = 2
MAX_GROUP_CHAT_PARTICIPANTS = 10
GROUP_CHAT_TURNS_PER_PARTICIPANT = 3
GROUP_CHAT_MIN_TURNS = 6


class GroupConversationError(ValueError):
    pass


@dataclass
class GroupConversationState:
    """Pure speaker/pass state for one Resident group conversation.

    `pass` means "nothing to add right now", not leaving the conversation.
    A later substantive contribution clears prior pass state, allowing every
    participant to speak again. A response with pass=True may still contain
    closing words; those words are saved by the caller but do not reset the
    ending vote.
    """

    participants: tuple[str, ...]
    initiator: str
    max_turns: int | None = None
    turn_count: int = 1
    _passed_since_progress: set[str] = field(default_factory=set, init=False)

    def __post_init__(self) -> None:
        if not MIN_GROUP_CHAT_PARTICIPANTS <= len(self.participants) <= MAX_GROUP_CHAT_PARTICIPANTS:
            raise GroupConversationError(
                f"group conversation requires {MIN_GROUP_CHAT_PARTICIPANTS}-"
                f"{MAX_GROUP_CHAT_PARTICIPANTS} participants"
            )
        if len(set(self.participants)) != len(self.participants):
            raise GroupConversationError("group conversation participants must be unique")
        if self.initiator not in self.participants:
            raise GroupConversationError("group conversation initiator must be a participant")
        if any(not name.strip() for name in self.participants):
            raise GroupConversationError("group conversation participant names must not be empty")
        if self.max_turns is None:
            self.max_turns = max(
                GROUP_CHAT_MIN_TURNS,
                len(self.participants) * GROUP_CHAT_TURNS_PER_PARTICIPANT,
            )
        elif self.max_turns < 1:
            raise GroupConversationError("group conversation max_turns must be positive")

    @property
    def passed_since_progress(self) -> frozenset[str]:
        return frozenset(self._passed_since_progress)

    @property
    def finished(self) -> bool:
        return (
            len(self._passed_since_progress) == len(self.participants)
            or self.turn_count >= (self.max_turns or GROUP_CHAT_MIN_TURNS)
        )

    def normalize_address(self, speaker: str, addressed_to: str | None) -> str | None:
        if addressed_to is None:
            return None
        cleaned = addressed_to.strip()
        if not cleaned or cleaned == speaker or cleaned not in self.participants:
            return None
        return cleaned

    def next_speaker(self, previous_speaker: str, addressed_to: str | None = None) -> str | None:
        if self.finished:
            return None

        addressed = self.normalize_address(previous_speaker, addressed_to)
        if addressed is not None and addressed not in self._passed_since_progress:
            return addressed

        try:
            start = self.participants.index(previous_speaker)
        except ValueError as exc:
            raise GroupConversationError("previous speaker is not a participant") from exc

        for offset in range(1, len(self.participants) + 1):
            candidate = self.participants[(start + offset) % len(self.participants)]
            if candidate == previous_speaker:
                continue
            if candidate in self._passed_since_progress:
                continue
            return candidate
        return None

    def record_response(
        self,
        speaker: str,
        *,
        say: str,
        passed: bool,
        addressed_to: str | None = None,
    ) -> tuple[str | None, str | None]:
        if speaker not in self.participants:
            raise GroupConversationError("speaker is not a participant")

        # Empty text with pass=False is still no conversational progress. Treat
        # it as a pass for scheduling so a malformed model response cannot spin.
        effective_pass = passed or not say.strip()
        if effective_pass:
            self._passed_since_progress.add(speaker)
        else:
            # A real new contribution can change the topic. Earlier passes are
            # stale and those Residents may naturally rejoin on the new topic.
            self._passed_since_progress.clear()

        self.turn_count += 1
        addressed = self.normalize_address(speaker, addressed_to)
        if self.finished:
            return None, addressed
        return self.next_speaker(speaker, addressed), addressed
