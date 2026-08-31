from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Callable


HOLO_ATTACH_WINDOW_DEFAULT_SEC = 300.0


class HoloAuthorizationError(RuntimeError):
    pass


@dataclass(frozen=True)
class HoloDiveBinding:
    dive_session_id: str
    attached_at: float


@dataclass(frozen=True)
class _PendingAttachWindow:
    dive_session_id: str
    expires_at: float


class HoloAuthorization:
    """Authorizes the single local Holo Addon for a Master-started Dive.

    Transport authentication is handled by Core's per-process local bridge
    secret. This class only proves that Master directly opened the current
    Dive and that the one-shot attach completed.
    """

    def __init__(self, *, now: Callable[[], float] = time.time) -> None:
        self._now = now
        self._pending: _PendingAttachWindow | None = None
        self._binding: HoloDiveBinding | None = None

    @property
    def binding(self) -> HoloDiveBinding | None:
        return self._binding

    @property
    def pending_dive_session_id(self) -> str | None:
        pending = self._pending
        if pending is None:
            return None
        if pending.expires_at <= self._now():
            self._pending = None
            return None
        return pending.dive_session_id

    def open_attach_window(
        self,
        dive_session_id: str,
        *,
        ttl_sec: float = HOLO_ATTACH_WINDOW_DEFAULT_SEC,
    ) -> None:
        cleaned = dive_session_id.strip()
        if not cleaned:
            raise ValueError("dive_session_id must not be empty")
        if ttl_sec <= 0:
            raise ValueError("ttl_sec must be positive")

        self._binding = None
        self._pending = _PendingAttachWindow(
            dive_session_id=cleaned,
            expires_at=self._now() + ttl_sec,
        )

    def attach(self) -> HoloDiveBinding:
        pending = self._pending
        if pending is None or pending.expires_at <= self._now():
            self._pending = None
            raise HoloAuthorizationError("No active Holo Dive attach window")

        binding = HoloDiveBinding(
            dive_session_id=pending.dive_session_id,
            attached_at=self._now(),
        )
        self._binding = binding
        self._pending = None
        return binding

    def require_attached(self) -> HoloDiveBinding:
        binding = self._binding
        if binding is None:
            raise HoloAuthorizationError("Holo Dive is not attached")
        return binding

    def restore_binding(self, binding: HoloDiveBinding) -> bool:
        if not binding.dive_session_id.strip():
            self._binding = None
            return False
        self._pending = None
        self._binding = binding
        return True

    def revoke(self) -> None:
        self._pending = None
        self._binding = None
