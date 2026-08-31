from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any


HOLO_EVENT_WAIT_MAX_SEC = 15.0
HOLO_EVENT_BUFFER_SIZE = 256


@dataclass(frozen=True)
class HoloEventWaitResult:
    events: tuple[dict[str, Any], ...]
    latest_event_id: int
    timed_out: bool


class HoloEventQueue:
    """Bounded in-memory semantic event queue for Holo.

    This class intentionally knows nothing about MCP identity or transport.
    Authorization is expected to happen at the adapter boundary before these
    methods are called.
    """

    def __init__(self, *, max_events: int = HOLO_EVENT_BUFFER_SIZE) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        self._events: deque[dict[str, Any]] = deque(maxlen=max_events)
        self._condition = asyncio.Condition()
        self._next_event_id = 1
        self._active_waiters = 0

    @property
    def latest_event_id(self) -> int:
        return self._next_event_id - 1

    @property
    def active_waiters(self) -> int:
        return self._active_waiters

    def read_after(self, after_event_id: int, *, limit: int = 50) -> tuple[dict[str, Any], ...]:
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if limit <= 0:
            return ()
        return tuple(
            dict(event)
            for event in self._events
            if int(event["event_id"]) > after_event_id
        )[:limit]

    async def publish(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        cleaned_type = event_type.strip()
        if not cleaned_type:
            raise ValueError("event_type must not be empty")

        async with self._condition:
            event = {
                "event_id": self._next_event_id,
                "type": cleaned_type,
                "payload": dict(payload),
            }
            self._next_event_id += 1
            self._events.append(event)
            self._condition.notify_all()
            return dict(event)

    async def wait_after(
        self,
        after_event_id: int,
        *,
        timeout_sec: float,
        limit: int = 50,
    ) -> HoloEventWaitResult:
        if after_event_id < 0:
            raise ValueError("after_event_id must be non-negative")
        if timeout_sec < 0:
            raise ValueError("timeout_sec must be non-negative")
        bounded_timeout = min(float(timeout_sec), HOLO_EVENT_WAIT_MAX_SEC)

        async with self._condition:
            existing = self.read_after(after_event_id, limit=limit)
            if existing:
                return HoloEventWaitResult(
                    events=existing,
                    latest_event_id=self.latest_event_id,
                    timed_out=False,
                )

            self._active_waiters += 1
            try:
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(
                            lambda: bool(self.read_after(after_event_id, limit=limit))
                        ),
                        timeout=bounded_timeout,
                    )
                except TimeoutError:
                    return HoloEventWaitResult(
                        events=(),
                        latest_event_id=self.latest_event_id,
                        timed_out=True,
                    )

                return HoloEventWaitResult(
                    events=self.read_after(after_event_id, limit=limit),
                    latest_event_id=self.latest_event_id,
                    timed_out=False,
                )
            finally:
                self._active_waiters -= 1
