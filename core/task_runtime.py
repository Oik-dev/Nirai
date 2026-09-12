"""Task queue, assignment and Agent result reporting for CoreServer.

CoreServer remains the sole owner of services and in-flight state. Keeping this
implementation on that owner preserves its restart, cancellation and injected
failure boundaries without introducing a second queue or lifecycle registry.
"""
from __future__ import annotations

import asyncio
from dataclasses import replace
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from websockets.asyncio.server import ServerConnection

from .agents import (
    AgentEvent, AgentResourceBusyError, AgentRuntimeManagerError,
    AgentSafetyError, AgentSessionStoreError, TERMINAL_RUN_STATES,
)
from .brains.base import BrainDriver, BrainError
from .brains.gemini import is_antigravity_model
from .memory import WorldMemoryError
from .protocol import make_message
from .residents.service import (
    HOLO_ADDON_BRAIN,
    RESIDENT_ROLE_COMMANDER,
    RESIDENT_ROLE_EXECUTOR,
    RESIDENT_ROLE_INTEGRATED_AUDITOR,
    ResidentError,
)
from .sessions.chat_store import ChatStoreError
from .task_queue import QueuedTaskRecord, TaskQueueStoreError
from .usage_budget import routing_windows_for_model, usage_is_hard_limited

LOGGER = logging.getLogger("nirai.core.server")
TASK_CONSULT_FOLLOWUP_TURN_LIMIT = 8
PRE_AGENT_TASK_RESULT_LIMIT = 128


class CoreTaskRuntimeMixin:
    """Task orchestration using CoreServer's shared services and durable queue."""

    @staticmethod
    def _agent_task_phase_for_state(state: object) -> str | None:
        return {
            "running": "running",
            "completed": "done",
            "failed": "failed",
            "interrupted": "interrupted",
            "cancelled": "cancelled",
        }.get(state)

    @staticmethod
    def _agent_task_result_text(
        resident: str,
        phase: str,
        snapshot_payload: dict[str, Any],
    ) -> str:
        session_snapshot = snapshot_payload["session"]
        events = snapshot_payload["events"]
        final_summary = session_snapshot.get("final_summary")
        latest_error = next(
            (
                candidate.get("payload", {}).get("message")
                for candidate in reversed(events)
                if candidate.get("type") == "error"
                and isinstance(candidate.get("payload"), dict)
                and isinstance(candidate.get("payload", {}).get("message"), str)
            ),
            None,
        )
        if phase == "done":
            detail = final_summary if isinstance(final_summary, str) and final_summary.strip() else "作業が完了しました"
            return f"Task完了: {detail}"
        if phase == "cancelled":
            return "Task停止: Masterの操作またはProvider停止により作業を終了しました"
        if phase == "interrupted":
            interruption_reason = session_snapshot.get("interruption_reason")
            if interruption_reason in {"provider_quota_exhausted", "provider_rate_limit"}:
                partial_work_path = session_snapshot.get("partial_work_path")
                preserved = (
                    " Partial Workは保存済みです。"
                    if isinstance(partial_work_path, str) and partial_work_path
                    else ""
                )
                return (
                    "Task中断: Provider利用制限へ到達したためAgentを停止し、指揮者へ制御を返しました。"
                    + preserved
                )
            return "Task中断: Core再起動のため作業は未完了です。再開、やり直し、または破棄を選べます"
        detail = latest_error if isinstance(latest_error, str) and latest_error.strip() else "作業を完了できませんでした"
        return f"Task失敗: {detail}"

    def _restore_agent_task_state(self) -> None:
        orphaned_terminal_results = 0
        for snapshot in self.agent_runtime.list_snapshots():
            origin_session_id = snapshot.origin_chat_session_id
            if not origin_session_id:
                continue
            if snapshot.run_state == "interrupted":
                if (
                    snapshot.recovered_by_agent_session_id is None
                    and snapshot.task_phase != "interrupted"
                ):
                    self.agent_runtime.update_task_metadata(
                        snapshot.agent_session_id,
                        task_phase="interrupted",
                    )
                continue
            terminal_phase = (
                self._agent_task_phase_for_state(snapshot.run_state)
                if snapshot.run_state in TERMINAL_RUN_STATES
                else None
            )
            phase = snapshot.task_phase or terminal_phase or "assigned"
            if snapshot.result_reported:
                if terminal_phase is not None and snapshot.task_phase != terminal_phase:
                    snapshot = self.agent_runtime.update_task_metadata(
                        snapshot.agent_session_id,
                        task_phase=terminal_phase,
                        result_reported=True,
                    )
                if (
                    terminal_phase is not None
                    and not snapshot.result_notified
                    and self.sessions.store.has_session(origin_session_id)
                ):
                    payload = self.agent_runtime.snapshot_payload(snapshot.agent_session_id)
                    text = self._agent_task_result_text(snapshot.resident, terminal_phase, payload)
                    chat_entry = self.sessions.find_task_entry(
                        origin_session_id,
                        snapshot.agent_session_id,
                    )
                    self._recovered_agent_notifications[snapshot.agent_session_id] = (
                        {
                            "task_id": snapshot.task_id,
                            "phase": terminal_phase,
                            "text": text,
                            "agent_session_id": snapshot.agent_session_id,
                        },
                        chat_entry,
                    )
                continue
            if snapshot.run_state not in TERMINAL_RUN_STATES:
                continue
            if not self.sessions.store.has_session(origin_session_id):
                # The origin Chat may have been deliberately deleted, or a QA
                # smoke may have used a synthetic origin. There is nowhere valid
                # to replay this terminal result, so skip it without turning
                # every later Core startup into a warning storm.
                orphaned_terminal_results += 1
                continue

            payload = self.agent_runtime.snapshot_payload(snapshot.agent_session_id)
            terminal_phase = terminal_phase or "failed"
            text = self._agent_task_result_text(snapshot.resident, terminal_phase, payload)
            chat_entry = self.sessions.find_task_entry(origin_session_id, snapshot.agent_session_id)
            if chat_entry is None:
                chat_entry = self.sessions.append_task(
                    origin_session_id,
                    snapshot.resident,
                    text,
                    task_id=snapshot.task_id,
                    agent_session_id=snapshot.agent_session_id,
                )
            self._record_public_memory_entry(chat_entry)
            self.agent_runtime.update_task_metadata(
                snapshot.agent_session_id,
                task_phase=terminal_phase,
                result_reported=True,
                result_notified=False,
            )
            self._recovered_agent_notifications[snapshot.agent_session_id] = (
                {
                    "task_id": snapshot.task_id,
                    "phase": terminal_phase,
                    "text": text,
                    "agent_session_id": snapshot.agent_session_id,
                },
                chat_entry,
            )
        if orphaned_terminal_results:
            LOGGER.info(
                "agent_task_recovery_orphans_skipped count=%s",
                orphaned_terminal_results,
            )

    async def _handle_agent_task_event(
        self,
        event: AgentEvent,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if event.type != "run_state":
            return None, None
        snapshot_payload = self.agent_runtime.snapshot_payload(event.agent_session_id)
        session_snapshot = snapshot_payload["session"]
        origin_session_id = session_snapshot.get("origin_chat_session_id")
        if not isinstance(origin_session_id, str) or not origin_session_id:
            return None, None
        phase = self._agent_task_phase_for_state(event.payload.get("state"))
        current_phase = session_snapshot.get("task_phase")
        if phase is None or current_phase == phase:
            return None, None
        if phase in {"done", "failed", "cancelled", "interrupted"}:
            # Provider work just consumed quota or hit a provider-side stop.
            # Refresh asynchronously after terminal state is durable so routing
            # of the next Task does not rely on a pre-run budget snapshot.
            if (
                phase == "interrupted"
                and session_snapshot.get("interruption_reason")
                in {"provider_quota_exhausted", "provider_rate_limit"}
                and isinstance(session_snapshot.get("provider"), str)
            ):
                # A concurrent non-force refresh may already be in flight. Keep
                # this durable-in-process requirement until the next routing
                # refresh actually bypasses the normal cache.
                self._usage_force_refresh_required.add(session_snapshot["provider"])
            self._schedule_usage_refresh(force=True)

        if phase == "interrupted":
            self.agent_runtime.update_task_metadata(
                event.agent_session_id,
                task_phase="interrupted",
            )
            return {
                "task_id": event.task_id,
                "phase": "interrupted",
                "text": self._agent_task_result_text(event.resident, "interrupted", snapshot_payload),
                "agent_session_id": event.agent_session_id,
                "working_dir": session_snapshot["working_dir"],
                "interruption_reason": session_snapshot.get("interruption_reason"),
                "partial_work_path": session_snapshot.get("partial_work_path"),
            }, None

        chat_entry: dict[str, Any] | None = None
        result_persisted = False
        text = f"{event.resident}が作業中です"
        if phase in {"done", "failed", "cancelled"}:
            text = self._agent_task_result_text(event.resident, phase, snapshot_payload)
            already_reported = session_snapshot.get("result_reported") is True
            if not already_reported:
                try:
                    chat_entry = self.sessions.find_task_entry(
                        origin_session_id,
                        event.agent_session_id,
                    )
                    if chat_entry is None:
                        chat_entry = await self._append_chat_entry_async(
                            self.sessions.append_task,
                            origin_session_id,
                            event.resident,
                            text,
                            task_id=event.task_id,
                            agent_session_id=event.agent_session_id,
                        )
                    self._record_public_memory_entry(chat_entry)
                    await self._publish_holo_public_entry(chat_entry)
                    result_persisted = True
                except (ChatStoreError, WorldMemoryError):
                    LOGGER.warning(
                        "agent_task_result_persist_failed task_id=%s agent_session_id=%s",
                        event.task_id,
                        event.agent_session_id,
                        exc_info=True,
                    )

        metadata_reported = (
            phase in {"done", "failed", "cancelled"}
            and (
                result_persisted
                or session_snapshot.get("result_reported") is True
            )
        )
        self.agent_runtime.update_task_metadata(
            event.agent_session_id,
            task_phase=phase,
            result_reported=metadata_reported if phase in {"done", "failed", "cancelled"} else None,
            result_notified=False if phase in {"done", "failed", "cancelled"} else None,
        )
        return {
            "task_id": event.task_id,
            "phase": phase,
            "text": text,
            "agent_session_id": event.agent_session_id,
            "working_dir": session_snapshot["working_dir"],
        }, chat_entry

    def _chat_session_has_active_agent_task(self, session_id: str) -> bool:
        return any(
            snapshot.origin_chat_session_id == session_id
            and self._agent_session_blocks_lifecycle(snapshot)
            for snapshot in self.agent_runtime.list_snapshots()
        )

    def _agent_task_text_from_session(self, session: dict[str, Any]) -> str | None:
        candidates: list[Path] = []
        working = session.get("working_dir")
        if isinstance(working, str) and working.strip():
            candidates.append(Path(working) / "task.md")
        task_id = session.get("task_id")
        if isinstance(task_id, str) and task_id.strip():
            candidates.append(
                self.agent_runtime.workspace_policy.default_workspace_root / task_id / "task.md"
            )
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            try:
                raw = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                continue
            if raw:
                return raw.splitlines()[0][:240]
        return None

    async def _send_agent_snapshot(
        self,
        websocket: ServerConnection,
        agent_session_id: str,
        message_id: str | None = None,
    ) -> None:
        self._require_world_managed_agent_session(agent_session_id)
        await websocket.send(make_message(
            "agent_session_snapshot",
            self._agent_snapshot_payload(agent_session_id),
            message_id,
        ))

    async def _send_active_agent_snapshots(self, websocket: ServerConnection) -> None:
        for snapshot in self.agent_runtime.list_snapshots():
            if not self._agent_session_is_world_managed(snapshot):
                continue
            if snapshot.run_state in TERMINAL_RUN_STATES and not self._agent_session_blocks_lifecycle(snapshot):
                continue
            await self._send_agent_snapshot(websocket, snapshot.agent_session_id)

    async def _send_recovered_agent_notifications(self, websocket: ServerConnection) -> None:
        for agent_session_id, (task_update, chat_entry) in list(self._recovered_agent_notifications.items()):
            await self._send_agent_snapshot(websocket, agent_session_id)
            if chat_entry is not None:
                await websocket.send(make_message("chat_append", {"entry": chat_entry}))
            await websocket.send(make_message("task_update", task_update))
            self.agent_runtime.update_task_metadata(
                agent_session_id,
                result_notified=True,
            )
            self._recovered_agent_notifications.pop(agent_session_id, None)
        if self._recovered_agent_notifications:
            return
        await self._send_session_list(websocket)

    def _provider_supports_agent_work(self, provider: str, model: str | None = None) -> bool:
        if not self.agent_runtime.supports_provider(provider):
            return False
        if provider == "gemini":
            return isinstance(model, str) and bool(model.strip()) and is_antigravity_model(model.strip())
        return True

    def _provider_can_agent_work(self, provider: str, model: str | None = None) -> bool:
        if not self._provider_supports_agent_work(provider, model):
            return False
        # A concrete adapter already installed in this Core process is itself
        # evidence of an available runtime (notably injected/test adapters).
        # Otherwise consult the provider's external CLI/key discovery.
        return (
            self.agent_runtime.has_initialized_provider(provider)
            or self._provider_is_available(provider)
        )

    def _resident_supports_agent_work(self, resident: Any) -> bool:
        return (
            isinstance(resident.brain, str)
            and resident.brain != HOLO_ADDON_BRAIN
            and self._provider_supports_agent_work(resident.brain, resident.brain_model)
        )

    def _resident_usage_hard_limited(self, resident: Any) -> bool:
        if not isinstance(resident.brain, str):
            return False
        if resident.brain in self._usage_force_refresh_required:
            # This provider was just observed terminating on quota/rate limit.
            # Until a fresh non-UNKNOWN fetch succeeds, do not immediately route
            # new work back to the same known-interrupted provider.
            return True
        return usage_is_hard_limited(
            self.usage_budget.snapshot(resident.brain),
            model=resident.brain_model,
        )

    def _resident_quota_sort_key(self, resident: Any, roster_index: int) -> tuple[int, float, int]:
        """Prefer fresher/larger provider headroom without inventing policy thresholds."""
        if not isinstance(resident.brain, str):
            return (0, -1.0, -roster_index)
        snapshot = self.usage_budget.snapshot(resident.brain)
        if snapshot is None or snapshot.stale or snapshot.status == "unknown":
            return (0, -1.0, -roster_index)
        windows = routing_windows_for_model(snapshot, model=resident.brain_model)
        remaining = [
            window.remaining_percent
            for window in windows
            if window.remaining_percent is not None
        ]
        if not remaining:
            return (1, -1.0, -roster_index)
        # Multiple windows constrain the same work together, so the narrowest
        # fresh window is the useful generic headroom signal.
        return (1, min(remaining), -roster_index)

    def _normal_task_candidates(self) -> tuple[Any, ...]:
        roster = list(self.resident_service.list_enabled())
        candidates = [
            resident
            for resident in roster
            if resident.role == RESIDENT_ROLE_EXECUTOR
            and self._resident_supports_agent_work(resident)
            and resident.brain is not None
            and self._provider_can_agent_work(resident.brain, resident.brain_model)
            and not self._resident_usage_hard_limited(resident)
        ]
        candidates.sort(
            key=lambda resident: self._resident_quota_sort_key(
                resident,
                roster.index(resident),
            ),
            reverse=True,
        )
        return tuple(candidates)

    def _normal_task_commander_fallback(self) -> Any | None:
        candidates = [
            resident
            for resident in self.resident_service.list_enabled()
            if resident.role == RESIDENT_ROLE_COMMANDER
            and self._resident_supports_agent_work(resident)
            and resident.brain is not None
            and self._provider_can_agent_work(resident.brain, resident.brain_model)
            and not self._resident_usage_hard_limited(resident)
        ]
        return candidates[0] if candidates else None

    def _integrated_audit_candidates(self) -> tuple[Any, ...]:
        roster = list(self.resident_service.list_enabled())
        candidates = [
            resident
            for resident in roster
            if resident.role == RESIDENT_ROLE_INTEGRATED_AUDITOR
            and self._resident_supports_agent_work(resident)
            and resident.brain is not None
            and self._provider_can_agent_work(resident.brain, resident.brain_model)
            and not self._resident_usage_hard_limited(resident)
        ]
        candidates.sort(
            key=lambda resident: self._resident_quota_sort_key(
                resident,
                roster.index(resident),
            ),
            reverse=True,
        )
        return tuple(candidates)

    def _restore_task_queue_state(self) -> None:
        try:
            state = self._task_queue_store.load()
            durable_agent_task_ids = {
                snapshot.task_id
                for snapshot in self.agent_runtime.list_snapshots()
            }
            recovered: list[QueuedTaskRecord] = []
            raw_records = ([state.active] if state.active is not None else []) + list(state.pending)
            for record in raw_records:
                if record.task_id in durable_agent_task_ids:
                    LOGGER.warning(
                        "task_queue_record_already_promoted task_id=%s skipped=true",
                        record.task_id,
                    )
                    continue
                if not self.sessions.store.has_session(record.origin_session_id):
                    raise TaskQueueStoreError(
                        f"Task Queue origin chat session is missing: {record.origin_session_id}"
                    )
                metadata_dir = self.agent_runtime.workspace_policy.task_metadata_dir(
                    record.task_id,
                )
                if Path(record.task_metadata_dir).resolve() != metadata_dir:
                    raise TaskQueueStoreError(
                        f"Task Queue metadata directory is invalid: {record.task_id}"
                    )
                if record.resident_name is not None:
                    # A provider can be temporarily absent after reboot/restore.
                    # Preserve the durable assignment and let dispatch wait/fail
                    # at actual execution instead of corrupting the whole queue.
                    self._resolve_direct_task_resident(
                        record.resident_name,
                        require_provider_available=False,
                    )
                if record.target_name is not None:
                    named = self._named_task_working_dir(
                        record.task_id,
                        record.target_name,
                    )
                    if Path(record.working_dir).resolve() != named:
                        raise TaskQueueStoreError(
                            f"Task Queue target directory does not match its target name: {record.task_id}"
                        )
                    working_dir = named
                else:
                    working_dir = self.agent_runtime.workspace_policy.resolve_working_dir(
                        record.working_dir,
                        task_id=record.task_id,
                    )
                recovered.append(replace(
                    record,
                    message_id=None,
                    working_dir=str(working_dir),
                    task_metadata_dir=str(metadata_dir),
                ))
            self._task_queue = recovered
            self._active_pre_agent_task = None
            self._task_queue_store.save(active=None, pending=self._task_queue)
            for index, record in enumerate(self._task_queue, start=1):
                self._pending_pre_agent_task_updates[record.task_id] = {
                    "task_id": record.task_id,
                    "phase": "queued",
                    "text": f"Taskは順番待ちです（{index}番目）",
                    "working_dir": record.working_dir,
                    "queue_position": index,
                    **({"target": record.target_name} if record.target_name is not None else {}),
                    **({"assigned_resident": record.resident_name, "assignment_policy": "direct"} if record.resident_name is not None else {}),
                }
            if (
                state.active is not None
                and state.active.task_id not in durable_agent_task_ids
            ):
                LOGGER.warning(
                    "task_queue_recovered_active task_id=%s queued_for_retry=true",
                    state.active.task_id,
                )
        except (
            TaskQueueStoreError,
            AgentRuntimeManagerError,
            AgentSafetyError,
            ResidentError,
            OSError,
            ValueError,
        ) as exc:
            self._task_queue = []
            self._active_pre_agent_task = None
            self._task_queue_store_error = str(exc)
            self._task_queue_store_error_recoverable = False
            LOGGER.error(
                "task_queue_restore_failed error_type=%s error=%s",
                type(exc).__name__,
                str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n"),
            )

    def _task_queue_persistence_blocked(self) -> bool:
        return (
            self._task_queue_store_error is not None
            and not self._task_queue_store_error_recoverable
        )

    def _persist_task_queue_state(self) -> None:
        if self._task_queue_persistence_blocked():
            raise AgentRuntimeManagerError(
                f"Task Queue persistence is unavailable: {self._task_queue_store_error}"
            )
        try:
            self._task_queue_store.save(
                active=self._active_pre_agent_task,
                pending=self._task_queue,
            )
        except TaskQueueStoreError as exc:
            self._task_queue_store_error = str(exc)
            self._task_queue_store_error_recoverable = True
            raise AgentRuntimeManagerError(
                f"Task Queue persistence failed: {exc}"
            ) from exc
        self._task_queue_store_error = None
        self._task_queue_store_error_recoverable = False

    def _activate_task_record(self, request: QueuedTaskRecord) -> None:
        if self._active_pre_agent_task is not None:
            raise AgentRuntimeManagerError("Task Queue already has an active pre-Agent Task")
        self._active_pre_agent_task = request
        try:
            self._persist_task_queue_state()
        except AgentRuntimeManagerError:
            self._active_pre_agent_task = None
            raise

    def _enqueue_task_record(self, request: QueuedTaskRecord) -> int:
        self._task_queue.append(request)
        try:
            self._persist_task_queue_state()
        except AgentRuntimeManagerError:
            self._task_queue.pop()
            raise
        return len(self._task_queue)

    def _requeue_active_task_record(self, task_id: str) -> int | None:
        active = self._active_pre_agent_task
        if active is None or active.task_id != task_id:
            return None
        previous_queue = list(self._task_queue)
        self._active_pre_agent_task = None
        self._task_queue.append(active)
        try:
            self._persist_task_queue_state()
        except AgentRuntimeManagerError:
            self._active_pre_agent_task = active
            self._task_queue = previous_queue
            raise
        return len(self._task_queue)

    def _release_active_task_record(self, task_id: str) -> None:
        active = self._active_pre_agent_task
        if active is None or active.task_id != task_id:
            return
        self._active_pre_agent_task = None
        try:
            self._persist_task_queue_state()
        except AgentRuntimeManagerError:
            # Keep the in-memory marker aligned with the durable file. Queue
            # dispatch freezes via _task_queue_store_error until the state can
            # be repaired instead of risking duplicate work after restart.
            self._active_pre_agent_task = active
            LOGGER.error(
                "task_queue_release_failed task_id=%s error=%s",
                task_id,
                self._task_queue_store_error,
            )

    def _task_flow_busy(self) -> bool:
        task = self._task_flow_task
        return task is not None and not task.done()

    def _task_work_pending(self) -> bool:
        return (
            self._task_flow_busy()
            or self._active_pre_agent_task is not None
            or bool(self._task_queue)
        )

    def _named_task_working_dir(self, task_id: str, target_name: str) -> Path:
        if task_id.startswith("IA-"):
            return self.agent_runtime.workspace_policy.named_integrated_audit_working_dir(
                target_name,
                task_id=task_id,
            )
        return self.agent_runtime.workspace_policy.named_working_dir(
            target_name,
            task_id=task_id,
        )

    def _prepare_task_request_paths(
        self,
        task_id: str,
        text: str,
        target_name: str | None,
    ) -> tuple[str, str]:
        working_dir = (
            None
            if target_name is None
            else self._named_task_working_dir(task_id, target_name)
        )
        metadata_dir = self.agent_runtime.workspace_policy.task_metadata_dir(task_id)
        if working_dir is None:
            working_dir = self.agent_runtime.workspace_policy.resolve_working_dir(None, task_id=task_id)
        try:
            (metadata_dir / "task.md").write_text(text.strip() + "\n", encoding="utf-8")
        except OSError as exc:
            raise AgentRuntimeManagerError("Agent task metadata could not be saved") from exc
        return str(working_dir), str(metadata_dir)

    def _start_task_flow(self, request: QueuedTaskRecord) -> None:
        task = asyncio.create_task(
            self._run_task_flow(
                request.task_id,
                request.text,
                request.message_id,
                request.origin_session_id,
                working_dir=request.working_dir,
                task_metadata_dir=request.task_metadata_dir,
                target_name=request.target_name,
                resident_name=request.resident_name,
            ),
            name=f"task-flow-{request.task_id}",
        )
        # This registration is intentionally synchronous. Shutdown and a
        # second Task request must observe the reservation before the Task Flow
        # reaches its first await.
        self._task_flow_task = task
        self._task_flow_origin_session_id = request.origin_session_id
        task.add_done_callback(self._task_flow_done)

    async def _refresh_task_queue_positions(self) -> None:
        for index, request in enumerate(self._task_queue, start=1):
            await self._send_task_update(
                request.task_id,
                "queued",
                f"Taskは順番待ちです（{index}番目）",
                working_dir=request.working_dir,
                extra={
                    "queue_position": index,
                    **({"target": request.target_name} if request.target_name is not None else {}),
                },
            )

    async def _dispatch_next_queued_task(self) -> None:
        if (
            self.agent_runtime.is_stopping()
            or self._task_queue_persistence_blocked()
            or self._task_flow_busy()
            or self._active_pre_agent_task is not None
            or not self._task_queue
        ):
            return
        previous_queue = list(self._task_queue)
        selected_index = next(
            (
                index
                for index, candidate in enumerate(previous_queue)
                if self.agent_runtime.resource_available(candidate.working_dir, read_only=False)
            ),
            None,
        )
        if selected_index is None:
            return
        request = previous_queue[selected_index]
        self._active_pre_agent_task = request
        self._task_queue = previous_queue[:selected_index] + previous_queue[selected_index + 1 :]
        try:
            self._persist_task_queue_state()
        except AgentRuntimeManagerError:
            self._active_pre_agent_task = None
            self._task_queue = previous_queue
            raise
        self._start_task_flow(request)
        await self._refresh_task_queue_positions()

    def _task_queue_dispatch_done(self, task: asyncio.Task[None]) -> None:
        if self._task_queue_dispatch_task is task:
            self._task_queue_dispatch_task = None
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "task_queue_dispatch_failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    def _schedule_task_queue_dispatch(self) -> None:
        if (
            self.agent_runtime.is_stopping()
            or self._task_queue_persistence_blocked()
            or self._active_pre_agent_task is not None
            or not self._task_queue
        ):
            return
        current = self._task_queue_dispatch_task
        if current is not None and not current.done():
            return
        task = asyncio.create_task(
            self._dispatch_next_queued_task(),
            name="task-queue-dispatch",
        )
        self._task_queue_dispatch_task = task
        task.add_done_callback(self._task_queue_dispatch_done)

    def _chat_session_has_active_task(self, session_id: str) -> bool:
        if (
            self._active_pre_agent_task is not None
            and self._active_pre_agent_task.origin_session_id == session_id
        ):
            return True
        if any(request.origin_session_id == session_id for request in self._task_queue):
            return True
        task = self._task_flow_task
        if (
            task is not None
            and not task.done()
            and self._task_flow_origin_session_id == session_id
        ):
            return True
        return self._chat_session_has_active_agent_task(session_id)

    async def _send_task_update(
        self,
        task_id: str,
        phase: str,
        text: str,
        *,
        message_id: str | None = None,
        agent_session_id: str | None = None,
        working_dir: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "phase": phase,
            "text": text,
        }
        if agent_session_id is not None:
            payload["agent_session_id"] = agent_session_id
            self._pending_pre_agent_task_updates.pop(task_id, None)
        if working_dir is not None:
            payload["working_dir"] = working_dir
        if extra:
            payload.update(extra)
        if agent_session_id is None:
            self._pending_pre_agent_task_updates[task_id] = dict(payload)
            if phase in {"failed", "cancelled", "done"}:
                # World delivery consumes the replay queue, but Holo may query
                # the result afterwards. Keep a separate bounded result tail.
                self._recent_pre_agent_task_results.pop(task_id, None)
                self._recent_pre_agent_task_results[task_id] = dict(payload)
                while len(self._recent_pre_agent_task_results) > PRE_AGENT_TASK_RESULT_LIMIT:
                    self._recent_pre_agent_task_results.pop(next(iter(self._recent_pre_agent_task_results)))

        websocket = self._world_connection
        if websocket is None:
            return
        try:
            await websocket.send(make_message("task_update", payload, message_id))
            if agent_session_id is None and phase in {"failed", "cancelled", "done"}:
                self._pending_pre_agent_task_updates.pop(task_id, None)
        except Exception:
            LOGGER.warning(
                "task_update_publish_failed task_id=%s phase=%s",
                task_id,
                phase,
                exc_info=True,
            )

    async def _send_pending_pre_agent_task_updates(self, websocket: ServerConnection) -> None:
        for task_id, payload in list(self._pending_pre_agent_task_updates.items()):
            await websocket.send(make_message("task_update", dict(payload)))
            if payload.get("phase") in {"failed", "cancelled", "done"}:
                if self._pending_pre_agent_task_updates.get(task_id) == payload:
                    self._pending_pre_agent_task_updates.pop(task_id, None)

    async def _prepare_task_consult_formation(self, participants: tuple[str, ...]) -> None:
        websocket = self._world_connection
        if websocket is None or len(participants) < 2:
            return
        try:
            if len(participants) == 2:
                first, second = participants
                await self._request_world_action(
                    websocket,
                    first,
                    "approach",
                    {"target": second},
                    tolerate_world_disconnect=True,
                )
                await self._request_world_action(
                    websocket,
                    first,
                    "face",
                    {"target": second},
                    tolerate_world_disconnect=True,
                )
                await self._request_world_action(
                    websocket,
                    second,
                    "face",
                    {"target": first},
                    tolerate_world_disconnect=True,
                )
            else:
                await self._request_world_action(
                    websocket,
                    participants[0],
                    "gather",
                    {"participants": list(participants)},
                    tolerate_world_disconnect=True,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.warning("task_consult_formation_failed", exc_info=True)

    async def _restore_task_consult_stand(self, participants: tuple[str, ...]) -> None:
        websocket = self._world_connection
        if websocket is None:
            return
        await self._restore_resident_chat_stand(websocket, participants)

    async def _face_task_consult_speaker(
        self,
        participants: tuple[str, ...],
        speaker: str,
    ) -> None:
        websocket = self._world_connection
        if websocket is None or len(participants) < 3:
            return
        tasks = [
            asyncio.create_task(
                self._request_world_action(
                    websocket,
                    name,
                    "face",
                    {"target": speaker},
                    timeout_sec=5.0,
                    tolerate_world_disconnect=True,
                )
            )
            for name in participants
            if name != speaker
        ]
        if not tasks:
            return
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if any(isinstance(result, asyncio.CancelledError) for result in results):
            raise asyncio.CancelledError
        if any(isinstance(result, BaseException) for result in results):
            LOGGER.warning("task_consult_face_speaker_failed speaker=%s", speaker)

    def _resolve_direct_task_resident(
        self,
        resident_name: str,
        *,
        require_provider_available: bool = True,
    ) -> Any:
        cleaned = resident_name.strip()
        matches = [name for name in self.resident_service.enabled_names if name.casefold() == cleaned.casefold()]
        if len(matches) != 1:
            raise AgentRuntimeManagerError(f"Direct Task Resident is not enabled: {resident_name}")
        resident = self.resident_service.load(matches[0])
        if resident.role not in {
            RESIDENT_ROLE_EXECUTOR,
            RESIDENT_ROLE_INTEGRATED_AUDITOR,
            RESIDENT_ROLE_COMMANDER,
        }:
            raise AgentRuntimeManagerError(
                f"Direct Task cannot use Resident Role {resident.role}: {resident.name}"
            )
        if resident.brain is None or resident.brain == HOLO_ADDON_BRAIN:
            raise AgentRuntimeManagerError(
                f"Direct Task Resident cannot perform Agent work: {resident.name}"
            )
        can_work = (
            self._provider_can_agent_work(resident.brain, resident.brain_model)
            if require_provider_available
            else self._provider_supports_agent_work(resident.brain, resident.brain_model)
        )
        if not can_work:
            raise AgentRuntimeManagerError(
                f"Direct Task Resident does not support Agent work: {resident.name}"
            )
        if require_provider_available and self._resident_usage_hard_limited(resident):
            raise AgentRuntimeManagerError(
                f"Direct Task Resident is at a provider hard limit: {resident.name}"
            )
        return resident

    async def _consult_task_residents(
        self,
        task_id: str,
        text: str,
        origin_session_id: str,
    ) -> tuple[Any | None, tuple[str, ...]]:
        residents = tuple(
            resident
            for resident in self.resident_service.list_enabled()
            if resident.brain is not None and resident.brain != HOLO_ADDON_BRAIN
        )
        participant_names = tuple(resident.name for resident in residents)
        await self._prepare_task_consult_formation(participant_names)
        consult_history: list[dict[str, Any]] = []
        latest_volunteer: dict[str, bool] = {}
        first_volunteer_order: dict[str, int] = {}

        async def consult_once(resident: Any, consult_round: int) -> bool:
            assert resident.brain is not None
            invocation_id = f"INV-{uuid4()}"
            driver: BrainDriver | None = None
            can_agent_work = self._provider_can_agent_work(resident.brain, resident.brain_model)
            try:
                driver = self._get_brain_driver(resident.brain)
                self._invocation_drivers[invocation_id] = driver
                self._task_consult_invocations.add(invocation_id)
                response = await driver.think(
                    invocation_id,
                    "consult",
                    {
                        "name": resident.name,
                        "persona": self.resident_service.read_persona(resident.name),
                        "brain_model": resident.brain_model,
                        "brain_reasoning_effort": resident.brain_reasoning_effort,
                    },
                    {
                        "task_id": task_id,
                        "task_text": text,
                        "can_agent_work": can_agent_work,
                        "current_residents": list(participant_names),
                        "skills": self.skill_registry.prompt_context(),
                        "consult_history": [dict(item) for item in consult_history],
                        "consult_round": consult_round,
                    },
                )
                effective_volunteer = response.volunteer is True and can_agent_work
                latest_volunteer[resident.name] = effective_volunteer
                if effective_volunteer and resident.name not in first_volunteer_order:
                    first_volunteer_order[resident.name] = len(first_volunteer_order)
                needs_followup = response.needs_followup is True
                if response.say:
                    await self._face_task_consult_speaker(participant_names, resident.name)
                    entry = await self._append_chat_entry_async(
                        self.sessions.append_resident_chat,
                        origin_session_id,
                        resident.name,
                        None,
                        response.say,
                    )
                    await self._publish_resident_chat_entry(entry, None)
                consult_history.append({
                    "resident": resident.name,
                    "say": response.say,
                    "volunteer": effective_volunteer,
                    "can_agent_work": can_agent_work,
                    "needs_followup": needs_followup,
                    "round": consult_round,
                })
                return needs_followup
            except (BrainError, ResidentError) as exc:
                # A later consult failure must not leave a stale earlier
                # volunteer=true eligible for assignment. Failure means the
                # Resident's current stance could not be confirmed.
                latest_volunteer[resident.name] = False
                LOGGER.warning(
                    "task_consult_brain_failed task_id=%s resident=%s provider=%s round=%s error_type=%s error=%s",
                    task_id,
                    resident.name,
                    resident.brain,
                    consult_round,
                    type(exc).__name__,
                    str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n"),
                )
                websocket = self._world_connection
                if websocket is not None:
                    try:
                        await websocket.send(make_message(
                            "notice",
                            {
                                "level": "WARN",
                                "text": f"{resident.name}はTask相談に参加できませんでした",
                            },
                        ))
                    except Exception:
                        LOGGER.warning(
                            "task_consult_notice_publish_failed task_id=%s resident=%s",
                            task_id,
                            resident.name,
                            exc_info=True,
                        )
                return False
            finally:
                self._task_consult_invocations.discard(invocation_id)
                if driver is not None:
                    self._invocation_drivers.pop(invocation_id, None)

        try:
            first_round_followup = [
                await consult_once(resident, 1)
                for resident in residents
            ]
            if any(first_round_followup):
                followup_turns = 0
                consult_round = 2
                unresolved = True
                while unresolved:
                    remaining_turns = TASK_CONSULT_FOLLOWUP_TURN_LIMIT - followup_turns
                    if remaining_turns < len(residents):
                        LOGGER.info(
                            "task_consult_followup_limit_reached task_id=%s followup_turns=%s next_round_size=%s",
                            task_id,
                            followup_turns,
                            len(residents),
                        )
                        raise AgentRuntimeManagerError(
                            f"Task相談が追加{TASK_CONSULT_FOLLOWUP_TURN_LIMIT}ターン上限に達し、"
                            "全員の追加巡を完了できないため担当を決定しません"
                        )
                    round_followup = [
                        await consult_once(resident, consult_round)
                        for resident in residents
                    ]
                    followup_turns += len(residents)
                    unresolved = any(round_followup)
                    consult_round += 1
                    if unresolved and followup_turns >= TASK_CONSULT_FOLLOWUP_TURN_LIMIT:
                        LOGGER.info(
                            "task_consult_followup_limit_reached task_id=%s followup_turns=%s",
                            task_id,
                            followup_turns,
                        )
                        raise AgentRuntimeManagerError(
                            f"Task相談が追加{TASK_CONSULT_FOLLOWUP_TURN_LIMIT}ターン上限に達しても"
                            "未解決のため担当を決定しません"
                        )
        finally:
            await self._restore_task_consult_stand(participant_names)

        eligible = [
            resident
            for resident in residents
            if latest_volunteer.get(resident.name) is True
            and resident.name in first_volunteer_order
        ]
        eligible.sort(key=lambda resident: first_volunteer_order[resident.name])
        return (eligible[0] if eligible else None), participant_names

    async def _run_task_flow(
        self,
        task_id: str,
        text: str,
        message_id: str | None,
        origin_session_id: str | None = None,
        *,
        working_dir: str | None = None,
        task_metadata_dir: str | None = None,
        target_name: str | None = None,
        resident_name: str | None = None,
    ) -> None:
        origin_session_id = origin_session_id or self.sessions.active_session_id
        try:
            if resident_name is None:
                await self._send_task_update(
                    task_id,
                    "consulting",
                    "利用可能な実行者とProvider利用枠を確認しています",
                    message_id=message_id,
                )
            if self.agent_runtime.is_stopping():
                raise AgentRuntimeManagerError(
                    "Agent Runtime is stopping; new Task execution is not available"
                )
            resolved_metadata_dir = self.agent_runtime.workspace_policy.task_metadata_dir(task_id)
            if (
                task_metadata_dir is not None
                and Path(task_metadata_dir).resolve() != resolved_metadata_dir
            ):
                raise AgentSafetyError(
                    "Agent task metadata directory must be runtime/workspace/<task_id>"
                )
            if target_name is not None:
                resolved_working_dir = self._named_task_working_dir(
                    task_id,
                    target_name,
                )
                if working_dir is None or Path(working_dir).resolve() != resolved_working_dir:
                    raise AgentSafetyError("Task target directory no longer matches its queued target")
            else:
                resolved_working_dir = self.agent_runtime.workspace_policy.resolve_working_dir(
                    working_dir,
                    task_id=task_id,
                )
            try:
                (resolved_metadata_dir / "task.md").write_text(text.strip() + "\n", encoding="utf-8")
            except OSError as exc:
                raise AgentRuntimeManagerError("Agent task metadata could not be saved") from exc

            assignment_policy = "direct"
            if resident_name is not None:
                resident_for_refresh = self._resolve_direct_task_resident(
                    resident_name,
                    require_provider_available=False,
                )
                if resident_for_refresh.brain in {"codex", "cursor"}:
                    await self._refresh_usage_budget({resident_for_refresh.brain})
                resident = self._resolve_direct_task_resident(resident_name)
                if task_id.startswith("IA-") and resident.role != RESIDENT_ROLE_INTEGRATED_AUDITOR:
                    raise AgentRuntimeManagerError(
                        "Integrated Audit Task must be assigned to an integrated_auditor Resident"
                    )
            else:
                assignment_policy = "role_routing"
                await self._refresh_usage_budget()
                candidates = self._normal_task_candidates()
                resident = candidates[0] if candidates else self._normal_task_commander_fallback()
                if resident is None:
                    await self._send_task_update(
                        task_id,
                        "failed",
                        "通常Taskを実行できる実行者Residentがいないため、Taskを終了しました",
                    )
                    return
            if resident.brain is None or not self._provider_can_agent_work(resident.brain, resident.brain_model):
                raise AgentRuntimeManagerError(
                    f"Selected Resident is no longer eligible for Agent work: {resident.name}"
                )
            if target_name is not None:
                latest_working_dir = self._named_task_working_dir(
                    task_id,
                    target_name,
                )
                if latest_working_dir != resolved_working_dir:
                    raise AgentSafetyError(
                        "Task target directory changed during consultation; Provider will not start"
                    )
                resolved_working_dir = latest_working_dir
            snapshot = await self.agent_runtime.start_session(
                task_id=task_id,
                resident=resident.name,
                provider=resident.brain,
                prompt=text,
                working_dir=str(resolved_working_dir),
                task_metadata_dir=str(resolved_metadata_dir),
                model=resident.brain_model,
                reasoning_effort=resident.brain_reasoning_effort,
                origin_chat_session_id=origin_session_id,
                purpose="integrated_audit" if task_id.startswith("IA-") else "work",
            )
            await self._send_task_update(
                task_id,
                "assigned",
                (
                    f"{resident.name}へ直接Taskを割り当てました"
                    if assignment_policy == "direct"
                    else f"{resident.name}へRoleに基づいてTaskを割り当てました"
                ),
                message_id=message_id,
                agent_session_id=snapshot.agent_session_id,
                working_dir=snapshot.working_dir,
                extra={
                    "assigned_resident": resident.name,
                    "assignment_policy": assignment_policy,
                },
            )
        except asyncio.CancelledError:
            await self._send_task_update(
                task_id,
                "cancelled",
                "Taskを停止しました" if resident_name is not None else "Task割り当てを停止しました",
                message_id=message_id,
            )
            raise
        except AgentResourceBusyError:
            position = self._requeue_active_task_record(task_id)
            await self._send_task_update(
                task_id,
                "queued",
                "必要なAgent resourceが使用中のためTaskを待機します",
                message_id=message_id,
                working_dir=working_dir,
                extra={"queue_position": position} if position is not None else None,
            )
        except (
            AgentRuntimeManagerError,
            AgentSafetyError,
            AgentSessionStoreError,
            ChatStoreError,
            ResidentError,
            WorldMemoryError,
        ) as exc:
            LOGGER.warning(
                "task_flow_failed task_id=%s error_type=%s error=%s",
                task_id,
                type(exc).__name__,
                str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n"),
            )
            await self._send_task_update(task_id, "failed", str(exc), message_id=message_id)
        finally:
            self._release_active_task_record(task_id)

    def _task_flow_done(self, task: asyncio.Task[None]) -> None:
        if self._task_flow_task is task:
            self._task_flow_task = None
            self._task_flow_origin_session_id = None
        self._schedule_task_queue_dispatch()
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "task_flow_unhandled_failure",
                exc_info=(type(error), error, error.__traceback__),
            )
