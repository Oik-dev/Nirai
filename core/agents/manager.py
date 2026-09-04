from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .base import AgentRunRequest, AgentRuntimeAdapter, AgentRuntimeError
from .codex_app_server import CodexAppServerAdapter
from .safety import AgentWorkspacePolicy
from .store import AgentSessionStore, AgentSessionStoreError
from .types import (
    AgentEvent,
    AgentEventType,
    AgentRunState,
    AgentSessionSnapshot,
    TERMINAL_RUN_STATES,
    utc_now_iso,
)


BroadcastEvent = Callable[[AgentEvent], Awaitable[None]]
_EVENT_PAYLOAD_CHAR_BUDGET = 32_000
_EVENT_STRING_LIMIT = 12_000
_EVENT_COLLECTION_LIMIT = 50
_SESSION_EVENT_PAYLOAD_CHAR_BUDGET = 2_000_000
_FINAL_SUMMARY_LIMIT = 8_000


class AgentRuntimeManagerError(RuntimeError):
    pass


class AgentRuntimeManager:
    """Own Agent Session lifecycle independently from conversational Brain calls."""

    _BLOCKING_EVENT_KINDS: dict[str, str] = {
        "approval_request": "approval",
        "question_request": "question",
    }
    _RUN_STATES: frozenset[str] = frozenset({
        "queued",
        "starting",
        "running",
        "waiting_for_master",
        "cancelling",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    })

    def __init__(
        self,
        root: Path,
        allowed_dirs: tuple[str, ...],
        *,
        adapters: dict[str, AgentRuntimeAdapter] | None = None,
        broadcast: BroadcastEvent | None = None,
        session_timeout_sec: float = 60.0 * 60.0,
        interrupt_timeout_sec: float = 3.0,
    ) -> None:
        self.root = root.resolve()
        self.workspace_policy = AgentWorkspacePolicy(self.root, allowed_dirs)
        self.store = AgentSessionStore(self.root)
        self._adapters = (
            adapters
            if adapters is not None
            else {"codex": CodexAppServerAdapter(self.workspace_policy)}
        )
        self._broadcast = broadcast
        self.session_timeout_sec = float(session_timeout_sec)
        if self.session_timeout_sec <= 0:
            raise AgentRuntimeManagerError("Agent Session timeout must be positive")
        self.interrupt_timeout_sec = float(interrupt_timeout_sec)
        if self.interrupt_timeout_sec <= 0:
            raise AgentRuntimeManagerError("Agent interrupt timeout must be positive")
        self._state_lock = asyncio.Lock()
        self._start_reserved = False
        self._start_idle = asyncio.Event()
        self._start_idle.set()
        self._stopping = False
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._pending: dict[tuple[str, str], tuple[str, asyncio.Future[dict[str, Any]]]] = {}
        self._snapshots = {
            snapshot.agent_session_id: snapshot
            for snapshot in self.store.list_snapshots()
        }
        self._event_payload_chars = {
            agent_session_id: sum(
                len(str(event.get("payload", {})))
                for event in self.store.read_events(agent_session_id)
            )
            for agent_session_id in self._snapshots
        }
        self._recover_interrupted_sessions()

    def set_broadcast(self, broadcast: BroadcastEvent | None) -> None:
        self._broadcast = broadcast

    def supports_provider(self, provider: str) -> bool:
        return provider in self._adapters

    def is_stopping(self) -> bool:
        return self._stopping

    def has_active_session(self) -> bool:
        return self._start_reserved or any(
            snapshot.run_state not in TERMINAL_RUN_STATES
            for snapshot in self._snapshots.values()
        )

    def list_snapshots(self) -> list[AgentSessionSnapshot]:
        return sorted(self._snapshots.values(), key=lambda item: item.updated_at, reverse=True)

    def snapshot_payload(self, agent_session_id: str, *, after_seq: int = 0) -> dict[str, Any]:
        snapshot = self._require_snapshot(agent_session_id)
        events = [
            event
            for event in self.store.read_events(agent_session_id)
            if isinstance(event.get("seq"), int) and event["seq"] > after_seq
        ]
        return {
            "session": snapshot.to_protocol(),
            "events": events,
        }

    async def start_session(
        self,
        *,
        task_id: str,
        resident: str,
        provider: str,
        prompt: str,
        working_dir: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        origin_chat_session_id: str | None = None,
    ) -> AgentSessionSnapshot:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise AgentRuntimeManagerError("Agent task prompt must not be empty")
        adapter = self._adapters.get(provider)
        if adapter is None:
            raise AgentRuntimeManagerError(f"Agent Runtime provider is not available: {provider}")

        async with self._state_lock:
            if self._stopping:
                raise AgentRuntimeManagerError(
                    "Agent Runtime is stopping; new Task execution is not available"
                )
            if self._start_reserved or any(
                snapshot.run_state not in TERMINAL_RUN_STATES
                for snapshot in self._snapshots.values()
            ):
                raise AgentRuntimeManagerError(
                    "Another Agent Session is already running; concurrent Task execution is not available yet"
                )
            self._start_reserved = True
            self._start_idle.clear()

        agent_session_id: str | None = None
        try:
            resolved_working_dir = self.workspace_policy.resolve_working_dir(working_dir, task_id=task_id)
            try:
                (resolved_working_dir / "task.md").write_text(cleaned_prompt + "\n", encoding="utf-8")
            except OSError as exc:
                raise AgentRuntimeManagerError("Agent task metadata could not be saved") from exc
            agent_session_id = f"AS-{uuid4()}"
            now = utc_now_iso()
            snapshot = AgentSessionSnapshot(
                task_id=task_id,
                agent_session_id=agent_session_id,
                resident=resident,
                provider=provider,
                working_dir=str(resolved_working_dir),
                run_state="starting",
                started_at=now,
                updated_at=now,
                origin_chat_session_id=origin_chat_session_id,
                task_phase="assigned" if origin_chat_session_id else None,
            )
            self.store.create(snapshot)
            self._snapshots[agent_session_id] = snapshot
            self._event_payload_chars[agent_session_id] = 0
            await self._record_event(agent_session_id, "run_state", {"state": "starting"})

            request = AgentRunRequest(
                task_id=task_id,
                agent_session_id=agent_session_id,
                resident=resident,
                provider=provider,
                prompt=cleaned_prompt,
                working_dir=resolved_working_dir,
                model=model,
                reasoning_effort=reasoning_effort,
            )
            async with self._state_lock:
                latest = self._require_snapshot(agent_session_id)
                start_blocked = (
                    self._stopping
                    or latest.run_state == "cancelling"
                    or latest.run_state in TERMINAL_RUN_STATES
                )
                if not start_blocked:
                    task = asyncio.create_task(
                        self._run_session(adapter, request),
                        name=f"agent-runtime-{agent_session_id}",
                    )
                    self._tasks[agent_session_id] = task
                    task.add_done_callback(
                        lambda finished, session_id=agent_session_id: self._task_done(session_id, finished)
                    )

            if start_blocked:
                latest = self._require_snapshot(agent_session_id)
                if latest.run_state not in TERMINAL_RUN_STATES:
                    await self._finish_session(agent_session_id, "cancelled", None)
                raise AgentRuntimeManagerError(
                    "Agent Runtime is stopping or the Agent Session was cancelled before provider start"
                )
            return self._snapshots[agent_session_id]
        finally:
            async with self._state_lock:
                self._start_reserved = False
                self._start_idle.set()

    def update_task_metadata(
        self,
        agent_session_id: str,
        *,
        task_phase: str | None = None,
        result_reported: bool | None = None,
        result_notified: bool | None = None,
    ) -> AgentSessionSnapshot:
        snapshot = self._require_snapshot(agent_session_id)
        changes: dict[str, Any] = {}
        if task_phase is not None:
            changes["task_phase"] = task_phase
        if result_reported is not None:
            changes["result_reported"] = result_reported
        if result_notified is not None:
            changes["result_notified"] = result_notified
        if not changes:
            return snapshot
        updated = snapshot.with_updates(**changes)
        self.store.save_snapshot(updated)
        self._snapshots[agent_session_id] = updated
        return updated

    async def respond(
        self,
        agent_session_id: str,
        request_id: str,
        kind: str,
        response: dict[str, Any],
    ) -> bool:
        key = (agent_session_id, request_id)
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            pending = self._pending.get(key)
            if (
                pending is None
                or pending[0] != kind
                or snapshot.pending_request_id != request_id
                or snapshot.pending_request_kind != kind
            ):
                return False
            future = pending[1]
            if future.done():
                return False
            snapshot = snapshot.with_updates(
                run_state="running",
                pending_request_id=None,
                pending_request_kind=None,
            )
            self.store.save_snapshot(snapshot)
            event, snapshot = self.store.append_event(
                snapshot,
                "run_state",
                {"state": "running", "resumed_from": kind, "request_id": request_id},
            )
            self._snapshots[agent_session_id] = snapshot
            self._pending.pop(key, None)

        await self._broadcast_event(event)
        future.set_result(dict(response))
        return True

    async def cancel(self, agent_session_id: str) -> bool:
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            if snapshot.run_state in TERMINAL_RUN_STATES or snapshot.run_state == "cancelling":
                return False
            snapshot = snapshot.with_updates(run_state="cancelling")
            self.store.save_snapshot(snapshot)
            event, snapshot = self.store.append_event(snapshot, "run_state", {"state": "cancelling"})
            self._snapshots[agent_session_id] = snapshot
            adapter = self._adapters.get(snapshot.provider)
            task = self._tasks.get(agent_session_id)

        await self._broadcast_event(event)
        if adapter is not None:
            await self._interrupt_adapter(adapter, agent_session_id)
        if task is not None and not task.done():
            task.cancel()
        elif task is None:
            await self._finish_session(agent_session_id, "cancelled", None)
        return True

    async def begin_stop(self) -> None:
        async with self._state_lock:
            self._stopping = True

    async def stop(self) -> None:
        await self.begin_stop()
        await self._start_idle.wait()

        async with self._state_lock:
            session_ids = [
                session_id
                for session_id, snapshot in self._snapshots.items()
                if snapshot.run_state not in TERMINAL_RUN_STATES
            ]
        for session_id in session_ids:
            try:
                await self.cancel(session_id)
            except AgentRuntimeManagerError:
                continue
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_session(
        self,
        adapter: AgentRuntimeAdapter,
        request: AgentRunRequest,
    ) -> None:
        async def emit(event_type: AgentEventType, payload: dict[str, Any]) -> None:
            await self._record_event(request.agent_session_id, event_type, payload)

        async def wait_for_master(
            request_id: str,
            kind: str,
            payload: dict[str, Any],
        ) -> dict[str, Any]:
            return await self._wait_for_master(request.agent_session_id, request_id, kind)

        provider_task = asyncio.create_task(
            adapter.run(request, emit=emit, wait_for_master=wait_for_master),
            name=f"agent-provider-{request.agent_session_id}",
        )
        try:
            done, _ = await asyncio.wait(
                {provider_task},
                timeout=self.session_timeout_sec,
                return_when=asyncio.ALL_COMPLETED,
            )
            if provider_task not in done:
                await self._record_event(request.agent_session_id, "run_state", {
                    "state": "cancelling",
                    "reason": "session_timeout",
                })
                await self._record_event(request.agent_session_id, "error", {
                    "message": f"Agent Session exceeded the {int(self.session_timeout_sec)} second limit.",
                    "code": "session_timeout",
                    "recoverable": False,
                })
                await self._interrupt_adapter(adapter, request.agent_session_id)
                cleanup_error = await self._cancel_provider_task(provider_task)
                if cleanup_error is not None:
                    await self._record_provider_cleanup_error(
                        request.agent_session_id,
                        cleanup_error,
                    )
                await self._finish_session(request.agent_session_id, "failed", None)
                return

            summary = provider_task.result()
            snapshot = self._require_snapshot(request.agent_session_id)
            terminal_state: AgentRunState = (
                "cancelled"
                if snapshot.run_state in {"cancelling", "cancelled"}
                else "completed"
            )
            await self._finish_session(request.agent_session_id, terminal_state, summary)
        except asyncio.CancelledError:
            cleanup_error = await self._cancel_provider_task(provider_task)
            if cleanup_error is not None:
                await self._record_provider_cleanup_error(
                    request.agent_session_id,
                    cleanup_error,
                )
                await self._finish_session(request.agent_session_id, "failed", None)
                return
            await self._finish_session(request.agent_session_id, "cancelled", None)
        except Exception as exc:
            if not provider_task.done():
                provider_task.cancel()
                await asyncio.gather(provider_task, return_exceptions=True)
            await self._record_event(request.agent_session_id, "error", {
                "message": str(exc) or type(exc).__name__,
                "recoverable": False,
            })
            await self._finish_session(request.agent_session_id, "failed", None)

    async def _cancel_provider_task(
        self,
        provider_task: asyncio.Task[str | None],
    ) -> BaseException | None:
        if not provider_task.done():
            provider_task.cancel()
        result = (await asyncio.gather(provider_task, return_exceptions=True))[0]
        if isinstance(result, asyncio.CancelledError):
            return None
        if isinstance(result, BaseException):
            return result
        return None

    async def _record_provider_cleanup_error(
        self,
        agent_session_id: str,
        error: BaseException,
    ) -> None:
        await self._record_event(agent_session_id, "error", {
            "message": str(error) or type(error).__name__,
            "code": "provider_cleanup_failed",
            "recoverable": False,
        })

    async def _interrupt_adapter(
        self,
        adapter: AgentRuntimeAdapter,
        agent_session_id: str,
    ) -> bool:
        try:
            return await asyncio.wait_for(
                adapter.cancel(agent_session_id),
                timeout=self.interrupt_timeout_sec,
            )
        except (asyncio.TimeoutError, AgentRuntimeError):
            return False
        except Exception:
            return False

    async def _wait_for_master(
        self,
        agent_session_id: str,
        request_id: str,
        kind: str,
    ) -> dict[str, Any]:
        key = (agent_session_id, request_id)
        async with self._state_lock:
            pending = self._pending.get(key)
            if pending is None or pending[0] != kind:
                raise AgentRuntimeManagerError(
                    f"Agent request is not pending: {agent_session_id}/{request_id}/{kind}"
                )
            future = pending[1]
        try:
            return await future
        except asyncio.CancelledError:
            async with self._state_lock:
                current = self._pending.get(key)
                if current is not None and current[1] is future:
                    self._pending.pop(key, None)
                    if not future.done():
                        future.cancel()
            raise

    async def _record_event(
        self,
        agent_session_id: str,
        event_type: AgentEventType,
        payload: dict[str, Any],
    ) -> AgentEvent:
        events_to_broadcast: list[AgentEvent] = []
        event_payload = _bounded_event_payload(dict(payload))
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            if event_type not in {"approval_request", "question_request", "plan", "run_state", "error"}:
                used_payload_chars = self._event_payload_chars.get(agent_session_id, 0)
                if used_payload_chars >= _SESSION_EVENT_PAYLOAD_CHAR_BUDGET:
                    event_payload = {
                        "truncated": True,
                        "message": "Agent Session event payload budget was exhausted.",
                    }
                else:
                    remaining = _SESSION_EVENT_PAYLOAD_CHAR_BUDGET - used_payload_chars
                    event_payload = _bounded_event_payload(event_payload, char_budget=remaining)

            if event_type == "run_state":
                state = event_payload.get("state")
                if isinstance(state, str) and state in self._RUN_STATES:
                    changes: dict[str, Any] = {"run_state": state}
                    provider_session_id = event_payload.pop("provider_session_id", None)
                    provider_turn_id = event_payload.pop("provider_turn_id", None)
                    if isinstance(provider_session_id, str) and provider_session_id:
                        changes["provider_session_id"] = provider_session_id
                    if isinstance(provider_turn_id, str) and provider_turn_id:
                        changes["provider_turn_id"] = provider_turn_id
                    snapshot = snapshot.with_updates(**changes)
                    self.store.save_snapshot(snapshot)

            blocking_kind = self._BLOCKING_EVENT_KINDS.get(event_type)
            if event_type == "plan" and event_payload.get("approval_required") is True:
                blocking_kind = "plan"
            if blocking_kind is not None:
                request_id = event_payload.get("request_id")
                if not isinstance(request_id, str) or not request_id:
                    raise AgentRuntimeManagerError(f"{event_type} requires request_id")
                key = (agent_session_id, request_id)
                if key in self._pending:
                    raise AgentRuntimeManagerError(f"duplicate Agent request_id: {request_id}")
                future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
                self._pending[key] = (blocking_kind, future)
                snapshot = snapshot.with_updates(
                    run_state="waiting_for_master",
                    pending_request_id=request_id,
                    pending_request_kind=blocking_kind,
                )
                self.store.save_snapshot(snapshot)

            event, snapshot = self.store.append_event(snapshot, event_type, event_payload)
            self._event_payload_chars[agent_session_id] = (
                self._event_payload_chars.get(agent_session_id, 0) + len(str(event_payload))
            )
            events_to_broadcast.append(event)

            if blocking_kind is not None:
                state_event, snapshot = self.store.append_event(
                    snapshot,
                    "run_state",
                    {
                        "state": "waiting_for_master",
                        "request_id": snapshot.pending_request_id,
                        "request_kind": blocking_kind,
                    },
                )
                self._event_payload_chars[agent_session_id] = (
                    self._event_payload_chars.get(agent_session_id, 0) + len(str(state_event.payload))
                )
                events_to_broadcast.append(state_event)

            self._snapshots[agent_session_id] = snapshot

        for event_to_broadcast in events_to_broadcast:
            await self._broadcast_event(event_to_broadcast)
        return event

    async def _finish_session(
        self,
        agent_session_id: str,
        state: AgentRunState,
        summary: str | None,
    ) -> None:
        events_to_broadcast: list[AgentEvent] = []
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            pending_id = snapshot.pending_request_id
            if pending_id is not None:
                pending = self._pending.pop((agent_session_id, pending_id), None)
                if pending is not None and not pending[1].done():
                    pending[1].cancel()

            already_terminal = snapshot.run_state == state
            bounded_summary = summary
            if isinstance(bounded_summary, str) and len(bounded_summary) > _FINAL_SUMMARY_LIMIT:
                bounded_summary = bounded_summary[: _FINAL_SUMMARY_LIMIT - 1].rstrip() + "…"
            snapshot = snapshot.with_updates(
                run_state=state,
                pending_request_id=None,
                pending_request_kind=None,
                final_summary=bounded_summary,
            )
            self.store.save_snapshot(snapshot)
            if not already_terminal:
                event, snapshot = self.store.append_event(snapshot, "run_state", {"state": state})
                events_to_broadcast.append(event)
            self._snapshots[agent_session_id] = snapshot

        for event in events_to_broadcast:
            await self._broadcast_event(event)

    async def _broadcast_event(self, event: AgentEvent) -> None:
        if self._broadcast is None:
            return
        try:
            await self._broadcast(event)
        except Exception:
            # Persistence is the commit point. A disconnected World can recover
            # the event later through agent_session_snapshot_request.
            return

    def _recover_interrupted_sessions(self) -> None:
        for agent_session_id, snapshot in tuple(self._snapshots.items()):
            if snapshot.run_state in TERMINAL_RUN_STATES:
                continue
            interrupted = snapshot.with_updates(
                run_state="interrupted",
                pending_request_id=None,
                pending_request_kind=None,
                final_summary="Core restarted before the Agent Session completed.",
            )
            self.store.save_snapshot(interrupted)
            _, interrupted = self.store.append_event(
                interrupted,
                "run_state",
                {"state": "interrupted", "message": "Core restarted before completion"},
            )
            self._snapshots[agent_session_id] = interrupted

    def _require_snapshot(self, agent_session_id: str) -> AgentSessionSnapshot:
        snapshot = self._snapshots.get(agent_session_id)
        if snapshot is None:
            raise AgentRuntimeManagerError(f"unknown Agent Session: {agent_session_id}")
        return snapshot

    def _task_done(self, agent_session_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(agent_session_id) is task:
            self._tasks.pop(agent_session_id, None)


def _bounded_event_payload(
    payload: dict[str, Any],
    *,
    char_budget: int = _EVENT_PAYLOAD_CHAR_BUDGET,
) -> dict[str, Any]:
    budget = [max(0, min(_EVENT_PAYLOAD_CHAR_BUDGET, int(char_budget)))]

    def bound(value: Any, depth: int = 0) -> Any:
        if budget[0] <= 0:
            return None
        if isinstance(value, str):
            limit = min(_EVENT_STRING_LIMIT, budget[0])
            if len(value) <= limit:
                budget[0] -= len(value)
                return value
            clipped = value[: max(0, limit - 1)].rstrip() + "…"
            budget[0] -= len(clipped)
            return clipped
        if isinstance(value, (bool, int, float)) or value is None:
            budget[0] -= min(32, budget[0])
            return value
        if depth >= 5:
            text = str(value)
            return bound(text, depth + 1)
        if isinstance(value, list):
            items: list[Any] = []
            for item in value[:_EVENT_COLLECTION_LIMIT]:
                if budget[0] <= 0:
                    break
                items.append(bound(item, depth + 1))
            if len(value) > len(items) and budget[0] > 0:
                items.append("…truncated…")
                budget[0] -= min(13, budget[0])
            return items
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            items = list(value.items())
            for key, item in items[:_EVENT_COLLECTION_LIMIT]:
                if budget[0] <= 0:
                    break
                result[str(key)] = bound(item, depth + 1)
            if len(items) > len(result) and budget[0] > 0:
                result["_truncated"] = True
                budget[0] -= min(16, budget[0])
            return result
        return bound(str(value), depth + 1)

    bounded = bound(payload)
    return bounded if isinstance(bounded, dict) else {"_truncated": True}
