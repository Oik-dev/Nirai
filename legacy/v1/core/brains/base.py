from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class BrainError(RuntimeError):
    """Base error for Brain driver failures."""


class BrainUnavailableError(BrainError):
    """Raised when the configured Brain CLI cannot be used."""


class BrainResponseError(BrainError):
    """Raised when a Brain response cannot be parsed after the allowed retry."""


@dataclass(frozen=True)
class BrainResponse:
    say: str
    actions: tuple[dict[str, Any], ...]
    passed: bool
    addressed_to: str | None = None
    volunteer: bool | None = None
    needs_followup: bool | None = None


class BrainDriver(Protocol):
    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse: ...

    async def cancel(self, invocation_id: str) -> bool: ...
