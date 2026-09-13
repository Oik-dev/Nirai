from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .base import (
    AgentProviderLimitError,
    AgentReviewTargetChangedError,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeAdapter,
    AgentRuntimeError,
)
from .antigravity_agent import AntigravityAgentAdapter
from .codex_app_server import CodexAppServerAdapter
from .cursor_acp import CursorAcpAdapter
from .safety import AgentSafetyError, AgentWorkspacePolicy
from .store import AgentSessionStore, AgentSessionStoreError
from .event_payloads import (
    _EVENT_PAYLOAD_CHAR_BUDGET,
    _EVENT_STRING_LIMIT,
    _EVENT_COLLECTION_LIMIT,
    _bounded_event_payload,
    _is_session_budget_exempt_event,
    _requires_complete_review_context,
)
from .types import (
    AGENT_RUN_STATES,
    AgentEvent,
    AgentEventType,
    AgentRunState,
    AgentSessionSnapshot,
    TERMINAL_RUN_STATES,
    utc_now_iso,
)


BroadcastEvent = Callable[[AgentEvent], Awaitable[None]]
_SESSION_EVENT_PAYLOAD_CHAR_BUDGET = 2_000_000
_FINAL_SUMMARY_LIMIT = 8_000


class AgentRuntimeManagerError(RuntimeError):
    pass


class AgentResourceBusyError(AgentRuntimeManagerError):
    """Requested Agent resource is temporarily busy, not permanently invalid."""


class AgentRuntimeManager:
    """Own Agent Session lifecycle independently from conversational Brain calls."""

    _BLOCKING_EVENT_KINDS: dict[str, str] = {
        "approval_request": "approval",
        "question_request": "question",
    }
    _RUN_STATES = AGENT_RUN_STATES

    def __init__(
        self,
        root: Path,
        allowed_dirs: tuple[str, ...],
        *,
        adapters: dict[str, AgentRuntimeAdapter] | None = None,
        broadcast: BroadcastEvent | None = None,
        session_timeout_sec: float = 60.0 * 60.0,
        interrupt_timeout_sec: float = 3.0,
        max_concurrent_sessions: int = 4,
    ) -> None:
        self.root = root.resolve()
        self.workspace_policy = AgentWorkspacePolicy(self.root, allowed_dirs)
        self.store = AgentSessionStore(self.root)
        if adapters is not None:
            self._adapters = dict(adapters)
            self._adapter_factories: dict[str, Callable[[], AgentRuntimeAdapter]] = {}
            self._provider_capabilities = {
                provider: frozenset(str(item) for item in getattr(adapter, "capabilities", ()))
                for provider, adapter in self._adapters.items()
            }
        else:
            # Provider runtimes are optional plugins, not Core prerequisites.
            # Do not construct them until a Task actually targets that provider.
            self._adapters: dict[str, AgentRuntimeAdapter] = {}
            self._adapter_factories = {
                "codex": lambda: CodexAppServerAdapter(self.workspace_policy),
                "cursor": lambda: CursorAcpAdapter(self.workspace_policy),
                "gemini": lambda: AntigravityAgentAdapter(self.workspace_policy),
            }
            self._provider_capabilities = {
                "codex": frozenset(CodexAppServerAdapter.capabilities),
                "cursor": frozenset(CursorAcpAdapter.capabilities),
                "gemini": frozenset(AntigravityAgentAdapter.capabilities),
            }
        self._broadcast = broadcast
        self._work_prompt_enricher: Callable[[str], str] | None = None
        self.session_timeout_sec = float(session_timeout_sec)
        if self.session_timeout_sec <= 0:
            raise AgentRuntimeManagerError("Agent Session timeout must be positive")
        self.interrupt_timeout_sec = float(interrupt_timeout_sec)
        if self.interrupt_timeout_sec <= 0:
            raise AgentRuntimeManagerError("Agent interrupt timeout must be positive")
        self.max_concurrent_sessions = int(max_concurrent_sessions)
        if self.max_concurrent_sessions <= 0:
            raise AgentRuntimeManagerError("Agent concurrency limit must be positive")
        self._state_lock = asyncio.Lock()
        self._start_reservations = 0
        self._unmaterialized_start_slots = 0
        self._reserved_write_workspaces: set[str] = set()
        # Read reservations carry their effective scope. Cursor read-only runs
        # over the Nirai repository root use a staging snapshot that physically
        # excludes runtime/, so those readers must not serialize unrelated
        # World Task workspaces under runtime/workspace. Other providers remain
        # conservative because their root read scope can include runtime/.
        self._reserved_read_workspaces: dict[tuple[str, bool], int] = {}
        self._start_idle = asyncio.Event()
        self._start_idle.set()
        self._stopping = False
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_tasks: dict[str, asyncio.Task[str | None | AgentRunResult]] = {}
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
        self._event_budget_sentinel: dict[str, AgentEvent] = {}
        self._repair_recovery_links()
        self._recover_interrupted_sessions()

    def set_broadcast(self, broadcast: BroadcastEvent | None) -> None:
        self._broadcast = broadcast

    def set_work_prompt_enricher(self, enricher: Callable[[str], str] | None) -> None:
        self._work_prompt_enricher = enricher

    def supports_provider(self, provider: str) -> bool:
        return provider in self._adapters or provider in self._adapter_factories

    def has_initialized_provider(self, provider: str) -> bool:
        """Whether this Core process already owns a usable provider adapter.

        This is intentionally distinct from external CLI/key discovery. Tests
        and embedded adapters may inject a concrete runtime directly, while a
        lazily configured provider may be supported without being initialized.
        """
        return provider in self._adapters

    def provider_capabilities(self, provider: str) -> frozenset[str]:
        # Capability discovery must not initialize an optional provider.
        return self._provider_capabilities.get(provider, frozenset())

    def _get_adapter(self, provider: str) -> AgentRuntimeAdapter:
        adapter = self._adapters.get(provider)
        if adapter is not None:
            return adapter
        factory = self._adapter_factories.get(provider)
        if factory is None:
            raise AgentRuntimeManagerError(f"Agent Runtime provider is not available: {provider}")
        try:
            adapter = factory()
        except Exception as exc:
            raise AgentRuntimeManagerError(
                f"Agent Runtime provider could not initialize: {provider}: {str(exc) or type(exc).__name__}"
            ) from exc
        self._adapters[provider] = adapter
        return adapter

    def discard_conversation_context(self, provider: str, conversation_id: str) -> None:
        adapter = self._adapters.get(provider)
        if adapter is None and provider in {"codex", "cursor"}:
            # Conversation-owned provider state is a disposable continuity cache.
            # Startup recovery may need to delete a crash-left home before that
            # optional provider has otherwise been initialized in this Core process.
            factory = self._adapter_factories.get(provider)
            if factory is not None:
                adapter = factory()
        if adapter is None:
            return
        discard = getattr(adapter, "discard_conversation_context", None)
        if callable(discard):
            discard(conversation_id)

    def is_stopping(self) -> bool:
        return self._stopping

    def has_active_session(self) -> bool:
        return self._start_reservations > 0 or any(
            self._session_holds_resources(snapshot)
            for snapshot in self._snapshots.values()
        )

    def resource_available(self, working_dir: str | Path, *, read_only: bool = False) -> bool:
        if self._active_session_count() >= self.max_concurrent_sessions:
            return False
        # Provider-less probes keep the conservative read scope. Only a start
        # with a known Adapter may exclude runtime/ from its read reservation.
        return not self._workspace_conflicts(
            self._workspace_key(Path(working_dir)),
            read_only=read_only,
        )

    def _active_session_count(self) -> int:
        return self._unmaterialized_start_slots + sum(
            1 for snapshot in self._snapshots.values() if self._session_holds_resources(snapshot)
        )

    def _workspace_conflicts(
        self,
        workspace_key: str,
        *,
        read_only: bool,
        read_excludes_runtime: bool = False,
        recovery_source_agent_session_id: str | None = None,
    ) -> bool:
        if read_only:
            return (
                any(
                    self._read_scope_overlaps_workspace(
                        workspace_key,
                        reserved,
                        excludes_runtime=read_excludes_runtime,
                    )
                    for reserved in self._reserved_write_workspaces
                )
                or any(
                    snapshot.agent_session_id != recovery_source_agent_session_id
                    and self._session_holds_exclusive_workspace(snapshot)
                    and self._read_scope_overlaps_workspace(
                        workspace_key,
                        self._workspace_key(Path(snapshot.working_dir)),
                        excludes_runtime=read_excludes_runtime,
                    )
                    for snapshot in self._snapshots.values()
                )
            )
        return (
            any(
                self._workspace_keys_overlap(workspace_key, reserved)
                for reserved in self._reserved_write_workspaces
            )
            or any(
                count > 0
                and self._read_scope_overlaps_workspace(
                    reserved,
                    workspace_key,
                    excludes_runtime=excludes_runtime,
                )
                for (reserved, excludes_runtime), count in self._reserved_read_workspaces.items()
            )
            or any(
                snapshot.agent_session_id != recovery_source_agent_session_id
                and self._session_holds_workspace(snapshot)
                and self._snapshot_workspace_conflicts_with_write(snapshot, workspace_key)
                for snapshot in self._snapshots.values()
            )
        )

    def list_snapshots(self, *, task_id: str | None = None) -> list[AgentSessionSnapshot]:
        snapshots = self._snapshots.values()
        return sorted(
            (snapshot for snapshot in snapshots if task_id is None or snapshot.task_id == task_id),
            key=lambda item: item.updated_at,
            reverse=True,
        )

    async def await_terminal_finalization(self) -> None:
        """Wait only for Sessions whose durable run state is already terminal.

        A terminal snapshot is persisted before the Core broadcast callback has
        finished reporting Task results into Chat/Memory. World reconnect must
        not attach in that short finalization window or it can race the replay
        cache and miss a terminal result. Running provider work is never waited.
        """
        async with self._state_lock:
            tasks = [
                task
                for agent_session_id, task in self._tasks.items()
                if not task.done()
                and (
                    snapshot := self._snapshots.get(agent_session_id)
                ) is not None
                and snapshot.run_state in TERMINAL_RUN_STATES
            ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def snapshot_payload(
        self,
        agent_session_id: str,
        *,
        after_seq: int = 0,
        event_limit: int | None = None,
        include_events: bool = True,
    ) -> dict[str, Any]:
        snapshot = self._require_snapshot(agent_session_id)
        if not include_events:
            events = []
        elif event_limit is not None and after_seq == 0:
            bounded_limit = max(1, int(event_limit))
            events = self.store.read_event_tail(agent_session_id, limit=bounded_limit)
        else:
            events = [
                event
                for event in self.store.read_events(agent_session_id)
                if isinstance(event.get("seq"), int) and event["seq"] > after_seq
            ]
            if event_limit is not None:
                events = events[: max(1, int(event_limit))]
        return {
            "session": snapshot.to_protocol(),
            "events": events,
            "recovery_options": self.recovery_options(agent_session_id),
        }

    def recovery_options(self, agent_session_id: str) -> list[str]:
        snapshot = self._require_snapshot(agent_session_id)
        if snapshot.conversation_id is not None:
            # Conversation-owned Agent Sessions are transport children, not
            # independently recoverable work. The Conversation owner decides
            # how to rebuild provider context after an interruption.
            return []
        if snapshot.run_state != "interrupted" or snapshot.recovered_by_agent_session_id is not None:
            return []
        options = ["rerun", "abandon"]
        if (
            snapshot.provider_session_id
            and "crash_resume" in self.provider_capabilities(snapshot.provider)
        ):
            options.insert(0, "resume")
        return options

    async def recover_session(
        self,
        agent_session_id: str,
        action: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
        resident_persona: str | None = None,
    ) -> AgentSessionSnapshot:
        snapshot = self._require_snapshot(agent_session_id)
        if snapshot.conversation_id is not None:
            raise AgentRuntimeManagerError(
                "Conversation-owned Agent Sessions must be recovered by their Conversation owner"
            )
        if snapshot.run_state != "interrupted" or snapshot.recovered_by_agent_session_id is not None:
            raise AgentRuntimeManagerError("Interrupted Agent Session recovery was already consumed or is unavailable")
        if action not in {"resume", "rerun", "abandon"}:
            raise AgentRuntimeManagerError("Agent recovery action must be resume, rerun, or abandon")
        if action == "resume":
            if not snapshot.provider_session_id:
                raise AgentRuntimeManagerError("Interrupted Agent Session has no resumable provider session")
            if "crash_resume" not in self.provider_capabilities(snapshot.provider):
                raise AgentRuntimeManagerError(
                    "Provider does not support crash-safe Agent Session resume; use rerun or abandon"
                )
        if action == "abandon":
            summary = "Master abandoned the interrupted Agent Session."
            events_to_broadcast: list[AgentEvent] = []
            async with self._state_lock:
                latest = self._require_snapshot(agent_session_id)
                if latest.run_state != "interrupted" or latest.recovered_by_agent_session_id is not None:
                    raise AgentRuntimeManagerError(
                        "Interrupted Agent Session recovery was already consumed or is unavailable"
                    )
                # Abandon is a recovery choice too. Commit the source terminal
                # state under the same lock used by resume/rerun reservation so
                # a concurrent recovery cannot start after Master discarded it.
                abandoned = latest.with_cleared_pending_request(
                    run_state="cancelled",
                    task_phase="cancelled",
                    final_summary=summary,
                )
                await asyncio.to_thread(self.store.save_snapshot, abandoned)
                status_event, abandoned = await asyncio.to_thread(
                    self.store.append_event,
                    abandoned,
                    "status_message",
                    {
                        "message": summary,
                        "recovery_action": "abandon",
                    },
                )
                state_event, abandoned = await asyncio.to_thread(
                    self.store.append_event,
                    abandoned,
                    "run_state",
                    {
                        "state": "cancelled",
                        "recovery_action": "abandon",
                    },
                )
                self._event_payload_chars[agent_session_id] = (
                    self._event_payload_chars.get(agent_session_id, 0)
                    + len(str(status_event.payload))
                    + len(str(state_event.payload))
                )
                self._snapshots[agent_session_id] = abandoned
                events_to_broadcast.extend((status_event, state_event))
            for event in events_to_broadcast:
                await self._broadcast_event(event)
            return abandoned

        task_path = self.workspace_policy.task_metadata_dir(snapshot.task_id) / "task.md"
        try:
            original_prompt = task_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AgentRuntimeManagerError("Interrupted Agent task metadata could not be read") from exc
        if not original_prompt:
            raise AgentRuntimeManagerError("Interrupted Agent task metadata is empty")
        provider_prompt = original_prompt
        provider_session_id = None
        if action == "resume":
            provider_session_id = snapshot.provider_session_id
            provider_prompt = (
                "Resume the interrupted Nirai task from this existing provider session. "
                "Continue from the last safe state instead of repeating completed work. "
                "Re-check the current workspace before making further changes."
            )

        recovered_agent_session_id = f"AS-{uuid4()}"
        async with self._state_lock:
            latest = self._require_snapshot(agent_session_id)
            if latest.run_state != "interrupted" or latest.recovered_by_agent_session_id is not None:
                raise AgentRuntimeManagerError(
                    "Interrupted Agent Session recovery was already consumed or is unavailable"
                )
            reserved_source = latest.with_updates(
                recovered_by_agent_session_id=recovered_agent_session_id,
            )
            self.store.save_snapshot(reserved_source)
            self._snapshots[agent_session_id] = reserved_source

        effective_model = snapshot.model if model is None else model
        effective_reasoning_effort = snapshot.reasoning_effort if reasoning_effort is None else reasoning_effort
        try:
            recovered = await self.start_session(
                task_id=snapshot.task_id,
                resident=snapshot.resident,
                provider=snapshot.provider,
                prompt=provider_prompt,
                working_dir=snapshot.working_dir,
                resident_persona=resident_persona,
                model=effective_model,
                reasoning_effort=effective_reasoning_effort,
                origin_chat_session_id=snapshot.origin_chat_session_id,
                read_only=snapshot.read_only,
                purpose=snapshot.purpose,
                conversation_id=snapshot.conversation_id,
                provider_session_id=provider_session_id,
                metadata_prompt=original_prompt,
                preallocated_agent_session_id=recovered_agent_session_id,
                recovery_source_agent_session_id=agent_session_id,
                recovery_action=action,
            )
        except BaseException as exc:
            child_exists = False
            child_started = False
            restore_source = False
            async with self._state_lock:
                latest = self._require_snapshot(agent_session_id)
                child_exists = recovered_agent_session_id in self._snapshots
                child_started = recovered_agent_session_id in self._tasks
                restore_source = (
                    latest.run_state == "interrupted"
                    and latest.recovered_by_agent_session_id == recovered_agent_session_id
                    and not child_started
                    and (isinstance(exc, asyncio.CancelledError) or not child_exists)
                )
                if restore_source:
                    restored = latest.with_updates(recovered_by_agent_session_id=None)
                    self.store.save_snapshot(restored)
                    self._snapshots[agent_session_id] = restored
            if child_exists and not child_started:
                await self._finish_session(
                    recovered_agent_session_id,
                    "cancelled",
                    "Recovery start was cancelled before the child Session began.",
                )
            if child_exists and not restore_source:
                await self._finish_session(
                    agent_session_id,
                    "cancelled",
                    f"Recovery {action} was consumed by {recovered_agent_session_id}.",
                )
            raise

        await self._finish_session(
            agent_session_id,
            "cancelled",
            f"Recovery {action} continued as {recovered.agent_session_id}.",
        )
        return recovered

    async def start_session(
        self,
        *,
        task_id: str,
        resident: str,
        provider: str,
        prompt: str,
        working_dir: str | None = None,
        task_metadata_dir: str | None = None,
        resident_persona: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        origin_chat_session_id: str | None = None,
        read_only: bool = False,
        purpose: str = "work",
        conversation_id: str | None = None,
        provider_session_id: str | None = None,
        metadata_prompt: str | None = None,
        preallocated_agent_session_id: str | None = None,
        recovery_source_agent_session_id: str | None = None,
        recovery_action: str | None = None,
    ) -> AgentSessionSnapshot:
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise AgentRuntimeManagerError("Agent task prompt must not be empty")
        provider_prompt = (
            self._work_prompt_enricher(cleaned_prompt)
            if purpose in {"work", "integrated_audit"} and self._work_prompt_enricher is not None
            else cleaned_prompt
        ).strip()
        if not provider_prompt:
            raise AgentRuntimeManagerError("Agent task prompt enrichment produced an empty prompt")
        adapter = self._get_adapter(provider)

        if read_only and working_dir is not None and working_dir.strip():
            resolved_working_dir = self.workspace_policy.resolve_read_only_working_dir(
                working_dir,
                task_id=task_id,
            )
        elif purpose == "integrated_audit" and working_dir is not None and working_dir.strip():
            resolved_working_dir = self.workspace_policy.resolve_integrated_audit_working_dir(
                working_dir,
                task_id=task_id,
            )
        else:
            resolved_working_dir = self.workspace_policy.resolve_working_dir(working_dir, task_id=task_id)
        resolved_metadata_dir = self.workspace_policy.task_metadata_dir(task_id)
        if (
            task_metadata_dir is not None
            and Path(task_metadata_dir).resolve() != resolved_metadata_dir
        ):
            raise AgentSafetyError(
                "Agent task metadata directory must be runtime/workspace/<task_id>"
            )
        workspace_key = self._workspace_key(resolved_working_dir)
        read_excludes_runtime = self._read_scope_excludes_runtime(
            provider=provider,
            read_only=read_only,
            workspace_key=workspace_key,
        )
        async with self._state_lock:
            if self._stopping:
                raise AgentRuntimeManagerError(
                    "Agent Runtime is stopping; new Task execution is not available"
                )
            if self._active_session_count() >= self.max_concurrent_sessions:
                raise AgentResourceBusyError("Agent concurrency budget is currently full")
            if self._workspace_conflicts(
                workspace_key,
                read_only=read_only,
                read_excludes_runtime=read_excludes_runtime,
                recovery_source_agent_session_id=recovery_source_agent_session_id,
            ):
                message = (
                    "Another Agent Session is writing to the same workspace"
                    if read_only
                    else "Another Agent Session is using the same workspace"
                )
                raise AgentResourceBusyError(message)
            self._start_reservations += 1
            self._unmaterialized_start_slots += 1
            if read_only:
                read_reservation = (workspace_key, read_excludes_runtime)
                self._reserved_read_workspaces[read_reservation] = (
                    self._reserved_read_workspaces.get(read_reservation, 0) + 1
                )
            else:
                self._reserved_write_workspaces.add(workspace_key)
            self._start_idle.clear()

        agent_session_id: str | None = None
        slot_materialized = False
        try:
            metadata_text = metadata_prompt.strip() if isinstance(metadata_prompt, str) and metadata_prompt.strip() else cleaned_prompt
            try:
                (resolved_metadata_dir / "task.md").write_text(metadata_text + "\n", encoding="utf-8")
            except OSError as exc:
                raise AgentRuntimeManagerError("Agent task metadata could not be saved") from exc
            agent_session_id = preallocated_agent_session_id or f"AS-{uuid4()}"
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
                provider_session_id=provider_session_id,
                model=model,
                reasoning_effort=reasoning_effort,
                read_only=read_only,
                purpose=purpose,
                conversation_id=conversation_id,
                origin_chat_session_id=origin_chat_session_id,
                task_phase="assigned" if origin_chat_session_id else None,
                recovery_source_agent_session_id=recovery_source_agent_session_id,
                recovery_action=recovery_action,
            )
            self.store.create(snapshot)
            self._snapshots[agent_session_id] = snapshot
            self._event_payload_chars[agent_session_id] = 0
            self._event_budget_sentinel.pop(agent_session_id, None)
            async with self._state_lock:
                self._unmaterialized_start_slots = max(0, self._unmaterialized_start_slots - 1)
                slot_materialized = True
            await self._record_event(agent_session_id, "run_state", {"state": "starting"})

            request = AgentRunRequest(
                task_id=task_id,
                agent_session_id=agent_session_id,
                resident=resident,
                provider=provider,
                prompt=provider_prompt,
                working_dir=resolved_working_dir,
                resident_persona=resident_persona.strip() if isinstance(resident_persona, str) and resident_persona.strip() else None,
                model=model,
                reasoning_effort=reasoning_effort,
                read_only=read_only,
                purpose=purpose,
                conversation_id=conversation_id,
                provider_session_id=provider_session_id,
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
        except BaseException as exc:
            # Once a durable snapshot exists it must never remain `starting`
            # without an owning provider task. Startup may fail while recording
            # the first event or preparing request state; converge that orphan to
            # a terminal snapshot even when event persistence itself is broken.
            # CancelledError is BaseException in 3.12+, so Exception-only handling
            # would leave a live workspace lock until Core restart.
            if agent_session_id is not None and agent_session_id not in self._tasks:
                latest = self._snapshots.get(agent_session_id)
                if latest is not None and latest.run_state not in TERMINAL_RUN_STATES:
                    terminal: AgentRunState = (
                        "cancelled" if isinstance(exc, asyncio.CancelledError) else "failed"
                    )
                    if terminal == "cancelled":
                        summary = "Agent startup was cancelled before the provider task started."
                    else:
                        summary = f"Agent startup failed: {str(exc) or type(exc).__name__}"[:_FINAL_SUMMARY_LIMIT]
                    failed = latest.with_cleared_pending_request(
                        run_state=terminal,
                        final_summary=summary,
                    )
                    try:
                        self.store.save_snapshot(failed)
                        self._snapshots[agent_session_id] = failed
                    except AgentSessionStoreError:
                        pass
            raise
        finally:
            async with self._state_lock:
                self._start_reservations = max(0, self._start_reservations - 1)
                if not slot_materialized:
                    self._unmaterialized_start_slots = max(0, self._unmaterialized_start_slots - 1)
                if read_only:
                    read_reservation = (workspace_key, read_excludes_runtime)
                    remaining_reads = max(
                        0,
                        self._reserved_read_workspaces.get(read_reservation, 0) - 1,
                    )
                    if remaining_reads == 0:
                        self._reserved_read_workspaces.pop(read_reservation, None)
                    else:
                        self._reserved_read_workspaces[read_reservation] = remaining_reads
                else:
                    self._reserved_write_workspaces.discard(workspace_key)
                if self._start_reservations == 0:
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
                snapshot.run_state != "waiting_for_master"
                or pending is None
                or pending[0] != kind
                or snapshot.pending_request_id != request_id
                or snapshot.pending_request_kind != kind
            ):
                return False
            future = pending[1]
            if future.done():
                return False
            snapshot = snapshot.with_cleared_pending_request(
                run_state="running",
            )
            self.store.save_snapshot(snapshot)
            event, snapshot = self.store.append_event(
                snapshot,
                "run_state",
                {"state": "running", "resumed_from": kind, "request_id": request_id},
            )
            self._snapshots[agent_session_id] = snapshot

        await self._broadcast_event(event)
        # A concurrent Session cancel may cancel the provider wait while this
        # resumed-state broadcast is in flight. In that case the Master
        # response was superseded by cancellation and must not resurrect the
        # request or raise InvalidStateError into the World handler.
        if future.cancelled():
            return False
        if future.done():
            return False
        future.set_result(dict(response))
        return True

    async def cancel(self, agent_session_id: str) -> bool:
        persistence_error: BaseException | None = None
        event: AgentEvent | None = None
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            if snapshot.run_state in TERMINAL_RUN_STATES or snapshot.run_state == "cancelling":
                return False
            provider_task = self._provider_tasks.get(agent_session_id)
            if provider_task is not None and provider_task.done():
                # Provider execution, including Adapter-owned cleanup, already
                # ended before this Cancel entered Nirai's lifecycle boundary.
                # Let the owning Manager task consume that result instead of
                # retroactively reclassifying success/failure as cancellation.
                return False
            snapshot = snapshot.with_updates(run_state="cancelling")
            # Cancellation is a safety action. Even if durable state cannot be
            # updated, close the in-memory approval gate before interrupting the
            # provider so a stale Master response cannot resume work.
            self._snapshots[agent_session_id] = snapshot
            try:
                self.store.save_snapshot(snapshot)
                event, snapshot = self.store.append_event(
                    snapshot,
                    "run_state",
                    {"state": "cancelling"},
                )
                self._snapshots[agent_session_id] = snapshot
            except (AgentSessionStoreError, OSError) as exc:
                persistence_error = exc
            adapter = self._adapters.get(snapshot.provider)
            task = self._tasks.get(agent_session_id)

        if adapter is not None and task is not None:
            self._mark_adapter_cancel_intent(adapter, agent_session_id)
        if event is not None:
            await self._broadcast_event(event)
        if adapter is not None:
            await self._interrupt_adapter(adapter, agent_session_id)
        if task is not None and not task.done():
            task.cancel()
        elif task is None:
            try:
                await self._finish_session(agent_session_id, "cancelled", None)
            except (AgentSessionStoreError, OSError) as exc:
                persistence_error = persistence_error or exc
        if persistence_error is not None:
            raise AgentRuntimeManagerError(
                "Agent cancellation could not persist its durable state; provider interruption was still attempted"
            ) from persistence_error
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
        self._provider_tasks[request.agent_session_id] = provider_task
        try:
            done, _ = await asyncio.wait(
                {provider_task},
                timeout=self.session_timeout_sec,
                return_when=asyncio.ALL_COMPLETED,
            )
            if provider_task not in done:
                self._mark_adapter_cancel_intent(adapter, request.agent_session_id)
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
                cleanup_error, completed_result = await self._cancel_provider_task(provider_task)
                if cleanup_error is not None:
                    await self._record_provider_cleanup_error(
                        request.agent_session_id,
                        cleanup_error,
                    )
                if completed_result is not None and completed_result.work_committed:
                    await self._finish_session(
                        request.agent_session_id,
                        "completed",
                        completed_result.summary,
                        allow_completed_from_cancelling=True,
                    )
                    return
                await self._finish_session(request.agent_session_id, "failed", None)
                return

            result = provider_task.result()
            summary = result.summary if isinstance(result, AgentRunResult) else result
            work_committed = isinstance(result, AgentRunResult) and result.work_committed
            snapshot = self._require_snapshot(request.agent_session_id)
            terminal_state: AgentRunState = (
                "completed"
                if work_committed
                else "cancelled"
                if snapshot.run_state in {"cancelling", "cancelled"}
                else "completed"
            )
            await self._finish_session(
                request.agent_session_id,
                terminal_state,
                summary,
                allow_completed_from_cancelling=work_committed,
            )
        except asyncio.CancelledError:
            cleanup_error, completed_result = await self._cancel_provider_task(provider_task)
            if cleanup_error is not None:
                await self._record_provider_cleanup_error(
                    request.agent_session_id,
                    cleanup_error,
                )
                await self._finish_session(request.agent_session_id, "failed", None)
                return
            if completed_result is not None and completed_result.work_committed:
                await self._finish_session(
                    request.agent_session_id,
                    "completed",
                    completed_result.summary,
                    allow_completed_from_cancelling=True,
                )
                return
            await self._finish_session(request.agent_session_id, "cancelled", None)
        except AgentReviewTargetChangedError as exc:
            await self._record_event(request.agent_session_id, "error", {
                "message": str(exc),
                "code": "review_target_changed",
                "recoverable": True,
                "recommended_action": "fresh_review",
            })
            await self._finish_session(
                request.agent_session_id,
                "failed",
                str(exc),
            )
        except AgentProviderLimitError as exc:
            await self._record_event(request.agent_session_id, "error", {
                "message": str(exc),
                "code": exc.code,
                "recoverable": True,
                "partial_work_path": exc.partial_work_path,
            })
            async with self._state_lock:
                snapshot = self._require_snapshot(request.agent_session_id)
                snapshot = snapshot.with_updates(
                    interruption_reason=exc.code,
                    partial_work_path=exc.partial_work_path,
                )
                await asyncio.to_thread(self.store.save_snapshot, snapshot)
                self._snapshots[request.agent_session_id] = snapshot
            summary = (
                "Provider usage limit interrupted the Agent Session. "
                "Partial staged work was preserved for commander handoff."
                if exc.partial_work_path is not None
                else "Provider usage limit interrupted the Agent Session."
            )
            await self._finish_session(
                request.agent_session_id,
                "interrupted",
                summary,
            )
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
        provider_task: asyncio.Task[str | None | AgentRunResult],
    ) -> tuple[BaseException | None, AgentRunResult | None]:
        if not provider_task.done():
            provider_task.cancel()
        result = (await asyncio.gather(provider_task, return_exceptions=True))[0]
        if isinstance(result, asyncio.CancelledError):
            return None, None
        if isinstance(result, BaseException):
            return result, None
        if isinstance(result, AgentRunResult):
            return None, result
        # A plain normal return after cancellation is not enough evidence that
        # Provider work was committed before the cancel request. Only an
        # explicit AgentRunResult may override the ordinary cancelled outcome.
        return None, None

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

    @staticmethod
    def _mark_adapter_cancel_intent(
        adapter: AgentRuntimeAdapter,
        agent_session_id: str,
    ) -> None:
        marker = getattr(adapter, "mark_cancel_intent", None)
        if callable(marker):
            marker(agent_session_id)

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
            if not future.done():
                future.cancel()
            raise
        finally:
            async with self._state_lock:
                current = self._pending.get(key)
                if current is not None and current[1] is future:
                    self._pending.pop(key, None)

    async def _record_event(
        self,
        agent_session_id: str,
        event_type: AgentEventType,
        payload: dict[str, Any],
    ) -> AgentEvent:
        events_to_broadcast: list[AgentEvent] = []
        raw_event_payload = dict(payload)
        if _requires_complete_review_context(event_type, raw_event_payload):
            event_payload = _bounded_event_payload(
                raw_event_payload,
                string_limit=_EVENT_PAYLOAD_CHAR_BUDGET,
            )
            if event_payload != raw_event_payload:
                raise AgentRuntimeManagerError(
                    "Pending file-change review context exceeds safe persisted event bounds; approval was not opened"
                )
        else:
            event_payload = _bounded_event_payload(raw_event_payload)
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            budget_sentinel = False
            if not _is_session_budget_exempt_event(event_type, event_payload):
                used_payload_chars = self._event_payload_chars.get(agent_session_id, 0)
                if used_payload_chars >= _SESSION_EVENT_PAYLOAD_CHAR_BUDGET:
                    existing_sentinel = self._event_budget_sentinel.get(agent_session_id)
                    if existing_sentinel is not None:
                        return existing_sentinel
                    event_payload = {
                        "truncated": True,
                        "message": "Agent Session event payload budget was exhausted; further ordinary detail events are suppressed.",
                    }
                    budget_sentinel = True
                else:
                    remaining = _SESSION_EVENT_PAYLOAD_CHAR_BUDGET - used_payload_chars
                    event_payload = _bounded_event_payload(event_payload, char_budget=remaining)

            if event_type == "run_state":
                state = event_payload.get("state")
                if isinstance(state, str) and state in self._RUN_STATES:
                    changes: dict[str, Any] = {}
                    provider_session_id = event_payload.pop("provider_session_id", None)
                    provider_turn_id = event_payload.pop("provider_turn_id", None)
                    if isinstance(provider_session_id, str) and provider_session_id:
                        changes["provider_session_id"] = provider_session_id
                    if isinstance(provider_turn_id, str) and provider_turn_id:
                        changes["provider_turn_id"] = provider_turn_id
                    # Provider-emitted lifecycle states are evidence, not
                    # authority over Nirai-owned terminal/cancel/Master gates.
                    # In particular, a late ordinary `running` event after an
                    # approval/question/plan request must not close the durable
                    # waiting_for_master gate while its pending request remains.
                    allow_run_state = (
                        state not in TERMINAL_RUN_STATES
                        and snapshot.run_state not in TERMINAL_RUN_STATES
                        and snapshot.run_state not in {"cancelling", "waiting_for_master"}
                    )
                    if allow_run_state:
                        changes["run_state"] = state
                    if changes:
                        snapshot = snapshot.with_updates(**changes)
                        await asyncio.to_thread(self.store.save_snapshot, snapshot)

            blocking_kind = self._BLOCKING_EVENT_KINDS.get(event_type)
            if event_type == "plan" and event_payload.get("approval_required") is True:
                blocking_kind = "plan"
            if (
                blocking_kind is not None
                and (
                    snapshot.run_state in TERMINAL_RUN_STATES
                    or snapshot.run_state == "cancelling"
                )
            ):
                # Cancel already closed the Master gate. Log the late request
                # but do not reopen pending approval / question / plan.
                blocking_kind = None
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
                    pending_request_payload=dict(event_payload),
                )
                await asyncio.to_thread(self.store.save_snapshot, snapshot)

            event, snapshot = await asyncio.to_thread(
                self.store.append_event,
                snapshot,
                event_type,
                event_payload,
            )
            self._event_payload_chars[agent_session_id] = (
                self._event_payload_chars.get(agent_session_id, 0) + len(str(event_payload))
            )
            events_to_broadcast.append(event)
            if budget_sentinel:
                self._event_budget_sentinel[agent_session_id] = event

            if blocking_kind is not None:
                state_event, snapshot = await asyncio.to_thread(
                    self.store.append_event,
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
        *,
        allow_completed_from_cancelling: bool = False,
    ) -> None:
        events_to_broadcast: list[AgentEvent] = []
        async with self._state_lock:
            snapshot = self._require_snapshot(agent_session_id)
            for key in [key for key in self._pending if key[0] == agent_session_id]:
                pending = self._pending.pop(key, None)
                if pending is not None and not pending[1].done():
                    pending[1].cancel()

            if snapshot.run_state in {"completed", "failed", "cancelled"}:
                self._snapshots[agent_session_id] = snapshot
            else:
                if (
                    snapshot.run_state == "cancelling"
                    and state == "completed"
                    and not allow_completed_from_cancelling
                ):
                    state = "cancelled"
                bounded_summary = summary
                if isinstance(bounded_summary, str) and len(bounded_summary) > _FINAL_SUMMARY_LIMIT:
                    bounded_summary = bounded_summary[: _FINAL_SUMMARY_LIMIT - 1].rstrip() + "…"
                snapshot = snapshot.with_cleared_pending_request(
                    run_state=state,
                    final_summary=bounded_summary,
                )
                await asyncio.to_thread(self.store.save_snapshot, snapshot)
                event, snapshot = await asyncio.to_thread(
                    self.store.append_event,
                    snapshot,
                    "run_state",
                    {"state": state},
                )
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

    def _repair_recovery_links(self) -> None:
        """Resolve the durable one-shot recovery reservation after a Core restart.

        A recovery source records the child Session id before the child is
        created. If no child snapshot exists, the crash happened before that
        durable point and the source may be offered again. If the child exists,
        the recovery choice was consumed and the source must never be reusable.
        """
        for agent_session_id, snapshot in tuple(self._snapshots.items()):
            child_id = snapshot.recovered_by_agent_session_id
            if child_id is None:
                continue
            child = self._snapshots.get(child_id)
            if child is None:
                if snapshot.run_state == "interrupted":
                    restored = snapshot.with_updates(recovered_by_agent_session_id=None)
                    self.store.save_snapshot(restored)
                    self._snapshots[agent_session_id] = restored
                continue
            if snapshot.run_state == "interrupted":
                consumed = snapshot.with_cleared_pending_request(
                    run_state="cancelled",
                    final_summary=f"Recovery continued as {child_id} before Core restart.",
                )
                self.store.save_snapshot(consumed)
                event, consumed = self.store.append_event(
                    consumed,
                    "run_state",
                    {"state": "cancelled", "recovered_by_agent_session_id": child_id},
                )
                self._event_payload_chars[agent_session_id] = (
                    self._event_payload_chars.get(agent_session_id, 0) + len(str(event.payload))
                )
                self._snapshots[agent_session_id] = consumed

    def _recover_interrupted_sessions(self) -> None:
        for agent_session_id, snapshot in tuple(self._snapshots.items()):
            if snapshot.conversation_id is not None and snapshot.run_state == "interrupted":
                self._cancel_conversation_owned_restart_session(snapshot)
                continue
            if snapshot.run_state in TERMINAL_RUN_STATES:
                continue
            if snapshot.conversation_id is not None:
                self._cancel_conversation_owned_restart_session(snapshot)
                continue
            interrupted = snapshot.with_cleared_pending_request(
                run_state="interrupted",
                final_summary="Core restarted before the Agent Session completed.",
            )
            self.store.save_snapshot(interrupted)
            _, interrupted = self.store.append_event(
                interrupted,
                "run_state",
                {"state": "interrupted", "message": "Core restarted before completion"},
            )
            self._snapshots[agent_session_id] = interrupted

    def _cancel_conversation_owned_restart_session(
        self,
        snapshot: AgentSessionSnapshot,
    ) -> None:
        conversation_id = snapshot.conversation_id
        if conversation_id is None:
            raise AgentRuntimeManagerError("Conversation-owned restart recovery requires conversation_id")
        cancelled = snapshot.with_cleared_pending_request(
            run_state="cancelled",
            provider_session_id=None,
            final_summary=(
                "Core restarted before the Conversation-owned Agent Session completed. "
                "The owning Conversation will rebuild provider context."
            ),
        )
        self.store.save_snapshot(cancelled)
        _, cancelled = self.store.append_event(
            cancelled,
            "run_state",
            {
                "state": "cancelled",
                "reason": "conversation_owner_restart",
                "message": "Conversation-owned Agent Session was closed after Core restart",
            },
        )
        self._snapshots[snapshot.agent_session_id] = cancelled
        try:
            self.discard_conversation_context(snapshot.provider, conversation_id)
        except Exception as exc:
            # The stale native id is already removed and the child Session is
            # terminal, so provider work cannot escape Conversation ownership.
            # Keep cleanup failure observable without blocking Core startup.
            event, cancelled = self.store.append_event(
                cancelled,
                "error",
                {
                    "code": "conversation_provider_context_cleanup_failed",
                    "message": str(exc) or type(exc).__name__,
                    "recoverable": True,
                },
            )
            self._event_payload_chars[snapshot.agent_session_id] = (
                self._event_payload_chars.get(snapshot.agent_session_id, 0)
                + len(str(event.payload))
            )
            self._snapshots[snapshot.agent_session_id] = cancelled

    def _session_holds_resources(self, snapshot: AgentSessionSnapshot) -> bool:
        if snapshot.run_state not in TERMINAL_RUN_STATES:
            return True
        task = self._tasks.get(snapshot.agent_session_id)
        return task is not None and not task.done()

    def _session_holds_workspace(self, snapshot: AgentSessionSnapshot) -> bool:
        # Restart interruptions reserve the tree until explicit recovery.
        # Provider-limit interruptions instead return control to the commander:
        # the workspace remains blocked only while the owning Manager task is
        # still finalizing, then the existing resource gate releases it safely.
        if snapshot.run_state == "interrupted":
            if snapshot.interruption_reason in {
                "provider_quota_exhausted",
                "provider_rate_limit",
            }:
                return self._session_holds_resources(snapshot)
            child_id = snapshot.recovered_by_agent_session_id
            return child_id is None or child_id not in self._snapshots
        return self._session_holds_resources(snapshot)

    def _session_holds_exclusive_workspace(self, snapshot: AgentSessionSnapshot) -> bool:
        if snapshot.run_state == "interrupted":
            return self._session_holds_workspace(snapshot)
        return self._session_holds_resources(snapshot) and not snapshot.read_only

    def _read_scope_excludes_runtime(
        self,
        *,
        provider: str,
        read_only: bool,
        workspace_key: str,
    ) -> bool:
        return (
            read_only
            and provider == "cursor"
            and workspace_key == self._workspace_key(self.root)
        )

    def _read_scope_overlaps_workspace(
        self,
        read_workspace_key: str,
        other_workspace_key: str,
        *,
        excludes_runtime: bool,
    ) -> bool:
        if not self._workspace_keys_overlap(read_workspace_key, other_workspace_key):
            return False
        if excludes_runtime:
            runtime_key = self._workspace_key(self.root / "runtime")
            if (
                other_workspace_key == runtime_key
                or other_workspace_key.startswith(runtime_key.rstrip(os.sep.casefold()) + os.sep.casefold())
            ):
                return False
        return True

    def _snapshot_workspace_conflicts_with_write(
        self,
        snapshot: AgentSessionSnapshot,
        write_workspace_key: str,
    ) -> bool:
        snapshot_key = self._workspace_key(Path(snapshot.working_dir))
        if not snapshot.read_only:
            return self._workspace_keys_overlap(snapshot_key, write_workspace_key)
        return self._read_scope_overlaps_workspace(
            snapshot_key,
            write_workspace_key,
            excludes_runtime=self._read_scope_excludes_runtime(
                provider=snapshot.provider,
                read_only=True,
                workspace_key=snapshot_key,
            ),
        )

    @staticmethod
    def _workspace_key(path: Path) -> str:
        return str(path.resolve()).casefold()

    @staticmethod
    def _workspace_keys_overlap(left: str, right: str) -> bool:
        if left == right:
            return True
        separator = os.sep.casefold()
        return left.startswith(right.rstrip(separator) + separator) or right.startswith(
            left.rstrip(separator) + separator
        )

    def _require_snapshot(self, agent_session_id: str) -> AgentSessionSnapshot:
        snapshot = self._snapshots.get(agent_session_id)
        if snapshot is None:
            raise AgentRuntimeManagerError(f"unknown Agent Session: {agent_session_id}")
        return snapshot

    def _task_done(self, agent_session_id: str, task: asyncio.Task[None]) -> None:
        if self._tasks.get(agent_session_id) is task:
            self._tasks.pop(agent_session_id, None)
        self._provider_tasks.pop(agent_session_id, None)
        snapshot = self._snapshots.get(agent_session_id)
        if snapshot is None:
            return
        adapter = self._adapters.get(snapshot.provider)
        clear_intent = getattr(adapter, "clear_cancel_intent", None) if adapter is not None else None
        if callable(clear_intent):
            clear_intent(agent_session_id)
