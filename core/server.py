from __future__ import annotations

import asyncio
import hmac
import json
import logging
import secrets
from pathlib import Path
from time import perf_counter, time as wallclock_time
from typing import Any, Callable
from uuid import uuid4

from websockets.asyncio.server import ServerConnection, serve

from .agents import (
    AgentEvent,
    AgentRuntimeManager,
    AgentRuntimeManagerError,
    AgentSafetyError,
    AgentSessionStoreError,
    TERMINAL_RUN_STATES,
)
from .brains.base import BrainDriver, BrainError, BrainUnavailableError
from .brains.claude_code import ClaudeCodeDriver
from .brains.codex import (
    CodexDriver,
    list_codex_models,
    load_codex_defaults,
    resolve_codex_command,
)
from .brains.cursor import CursorDriver, list_cursor_models, resolve_cursor_command
from .brains.gemini import (
    GeminiDriver,
    GEMINI_DEFAULT_MODEL,
    is_antigravity_model,
    list_gemini_models,
    load_gemini_api_key,
)
from .config import ConfigError, NiraiConfig, save_audio_volume
from .conversation import GroupConversationError, GroupConversationState
from .holo import (
    HOLO_ATTACH_WINDOW_DEFAULT_SEC,
    HoloAuthorization,
    HoloAuthorizationError,
    HoloDiveBinding,
    HoloEventQueue,
    HoloEventWaitResult,
)
from .memory import (
    PrivateMemoryError,
    PrivateMemoryService,
    WorldMemoryError,
    WorldMemoryRetriever,
    WorldMemoryRetrieverError,
    WorldMemoryService,
)
from .protocol import ProtocolError, make_message, parse_message, time_of_day
from .residents.service import HOLO_ADDON_BRAIN, ResidentError, ResidentService
from .sessions.chat_store import ChatStore, ChatStoreError
from .sessions.manager import SessionManager
from .skills import SkillRegistry
from .task_queue import (
    QueuedTaskRecord,
    TASK_QUEUE_PENDING_LIMIT,
    TASK_QUEUE_TEXT_LIMIT,
    TaskQueueStore,
    TaskQueueStoreError,
)


CORE_HOST = "127.0.0.1"
RESIDENT_CHAT_STAND_CLEANUP_TIMEOUT_SEC = 0.2
TASK_CONSULT_CANCEL_TIMEOUT_SEC = 5.0
TASK_CONSULT_FOLLOWUP_TURN_LIMIT = 8
LOGGER = logging.getLogger("nirai.core.server")


def _write_holo_binding_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _replace_holo_binding_file(source: Path, target: Path) -> None:
    source.replace(target)


class CoreServer:
    def __init__(
        self,
        config: NiraiConfig,
        *,
        port_override: int | None = None,
        brain_driver: BrainDriver | None = None,
        holo_local_secret: str | None = None,
        world_secret: str | None = None,
        holo_now: Callable[[], float] = wallclock_time,
        holo_binding_write_text: Callable[[Path, str], None] = _write_holo_binding_text,
        holo_binding_replace: Callable[[Path, Path], None] = _replace_holo_binding_file,
    ) -> None:
        self.config = config
        self.host = CORE_HOST
        self.port = config.core.port if port_override is None else port_override
        self._server: Any | None = None
        self._world_connection: ServerConnection | None = None
        self._brain_driver_override = brain_driver
        self._brain_drivers: dict[str, BrainDriver] = {}
        self._invocation_drivers: dict[str, BrainDriver] = {}
        self._brain_call_lock = asyncio.Lock()
        self._provider_models_cache: dict[str, list[dict[str, Any]]] = {}
        self._provider_catalog_tasks: dict[str, asyncio.Task[None]] = {}
        self._action_waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._response_tasks: dict[str, asyncio.Task[None]] = {}
        self._resident_chat_tasks: set[asyncio.Task[Any]] = set()
        self._resident_chat_invocations: set[str] = set()
        self._task_flow_task: asyncio.Task[None] | None = None
        self._task_flow_origin_session_id: str | None = None
        self._task_consult_invocations: set[str] = set()
        self._task_queue_store = TaskQueueStore(config.root)
        self._task_queue: list[QueuedTaskRecord] = []
        self._active_pre_agent_task: QueuedTaskRecord | None = None
        self._task_queue_dispatch_task: asyncio.Task[None] | None = None
        self._request_invocations: dict[str, set[str]] = {}
        self._cancelled_requests: set[str] = set()
        self._agent_task_chat_sessions: dict[str, str] = {}
        self._agent_task_phases: dict[str, str] = {}
        self._agent_task_reported: set[str] = set()
        self._recovered_agent_notifications: dict[str, tuple[dict[str, Any], dict[str, Any] | None]] = {}
        self._pending_pre_agent_task_updates: dict[str, dict[str, Any]] = {}
        self._holo_events = HoloEventQueue()
        self._holo_now = holo_now
        self._holo_authorization = HoloAuthorization(now=holo_now)
        self._holo_local_secret = holo_local_secret
        self._world_secret = world_secret or secrets.token_urlsafe(48)
        self._holo_binding_write_text = holo_binding_write_text
        self._holo_binding_replace = holo_binding_replace
        self._holo_current_dive_session_id: str | None = None
        self.audio_volume = config.world.audio_volume
        self.resident_service = ResidentService(config.root, config.residents_enabled)
        self.private_memory = PrivateMemoryService(config.root)
        self.world_memory = WorldMemoryService(config.root)
        self.world_retriever = WorldMemoryRetriever(config.root)
        self.sessions = SessionManager(ChatStore(config.root / "runtime" / "chat_sessions"))
        self.agent_runtime = AgentRuntimeManager(
            config.root,
            config.tasks_allowed_dirs,
            broadcast=self._broadcast_agent_event,
        )
        self.skill_registry = SkillRegistry(config.root / "skills")
        self._task_queue_store_error: str | None = None
        self._restore_agent_task_state()
        self._restore_task_queue_state()
        self._restore_holo_binding_state()

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    def _holo_state_path(self):
        return self.config.root / "runtime" / "holo" / "state.json"

    def _holo_binding_path(self):
        return self.config.root / "runtime" / "holo" / "binding.json"

    def _restore_holo_binding_state(self) -> None:
        try:
            state = json.loads(self._holo_state_path().read_text(encoding="utf-8"))
            current_dive_session_id = state.get("current_dive_session_id")
            if not isinstance(current_dive_session_id, str) or not current_dive_session_id.strip():
                return
            self._holo_current_dive_session_id = current_dive_session_id

            raw_binding = json.loads(self._holo_binding_path().read_text(encoding="utf-8"))
            if raw_binding.get("dive_session_id") != current_dive_session_id:
                return
            attached_at = raw_binding.get("attached_at")
            if not isinstance(attached_at, (int, float)):
                return
            binding = HoloDiveBinding(
                dive_session_id=current_dive_session_id,
                attached_at=float(attached_at),
            )
            if self._holo_authorization.restore_binding(binding):
                LOGGER.info(
                    "holo_binding_restored dive_session_id=%s",
                    binding.dive_session_id,
                )
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return

    def _persist_holo_binding(self, binding: HoloDiveBinding) -> None:
        path = self._holo_binding_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "dive_session_id": binding.dive_session_id,
            "attached_at": binding.attached_at,
        }
        temporary = path.with_name(f"{path.name}.{uuid4()}.tmp")
        try:
            self._holo_binding_write_text(
                temporary,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
            self._holo_binding_replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("holo_binding_temp_clear_failed", exc_info=True)

    def _clear_holo_binding_file(self) -> None:
        try:
            self._holo_binding_path().unlink(missing_ok=True)
        except OSError:
            LOGGER.warning("holo_binding_file_clear_failed", exc_info=True)

    def holo_open_attach_window(
        self,
        dive_session_id: str,
        *,
        attach_expires_at_ms: float | None = None,
    ) -> None:
        cleaned = dive_session_id.strip()
        if not cleaned:
            raise ValueError("dive_session_id must not be empty")

        # A repeated delivery of the same Dive must be idempotent. In
        # particular, an ACK lost after attach must never revoke the existing
        # one-shot binding and mint a fresh attach opportunity.
        binding = self._holo_authorization.binding
        if binding is not None and binding.dive_session_id == cleaned:
            self._holo_current_dive_session_id = cleaned
            LOGGER.info("holo_attach_window_duplicate_attached dive_session_id=%s", cleaned)
            return
        pending_dive_session_id = self._holo_authorization.pending_dive_session_id
        if pending_dive_session_id == cleaned:
            self._holo_current_dive_session_id = cleaned
            LOGGER.info("holo_attach_window_duplicate_pending dive_session_id=%s", cleaned)
            return

        ttl_sec = HOLO_ATTACH_WINDOW_DEFAULT_SEC
        if attach_expires_at_ms is not None:
            remaining_sec = (float(attach_expires_at_ms) / 1000.0) - self._holo_now()
            if remaining_sec <= 0:
                raise HoloAuthorizationError("Holo Dive attach window has expired")
            ttl_sec = min(HOLO_ATTACH_WINDOW_DEFAULT_SEC, remaining_sec)

        self._holo_current_dive_session_id = cleaned
        self._holo_authorization.open_attach_window(cleaned, ttl_sec=ttl_sec)
        self._clear_holo_binding_file()
        LOGGER.info(
            "holo_attach_window_opened dive_session_id=%s ttl_sec=%.3f",
            cleaned,
            ttl_sec,
        )

    def holo_addon_state(self) -> dict[str, Any]:
        """Return the non-secret Holo lifecycle state visible to Nirai World."""
        binding = self._holo_authorization.binding
        pending_dive_session_id = self._holo_authorization.pending_dive_session_id
        if binding is not None:
            local_bridge_state = "attached"
            current_dive_session_id = binding.dive_session_id
        elif pending_dive_session_id is not None:
            local_bridge_state = "attach_waiting"
            current_dive_session_id = pending_dive_session_id
        else:
            local_bridge_state = "not_started"
            current_dive_session_id = self._holo_current_dive_session_id
        return {
            "local_bridge_state": local_bridge_state,
            "current_dive_session_id": current_dive_session_id,
        }

    async def _send_holo_addon_state(
        self,
        websocket: ServerConnection | None = None,
        message_id: str | None = None,
    ) -> None:
        target = websocket or self._world_connection
        if target is None:
            return
        await target.send(make_message("holo_addon_state", self.holo_addon_state(), message_id))

    def holo_attach(self) -> HoloDiveBinding:
        # Validate first without consuming the one-shot window. Durable state is
        # the commit barrier: only a binding that reached binding.json may be
        # exposed as attached in memory or to World/Local Client.
        binding = self._holo_authorization.prepare_attach()
        try:
            self._persist_holo_binding(binding)
        except OSError as exc:
            LOGGER.warning(
                "holo_binding_persist_failed dive_session_id=%s",
                binding.dive_session_id,
                exc_info=True,
            )
            # pending remains untouched with its original absolute expires_at,
            # so Master may retry within the same five-minute window.
            raise HoloAuthorizationError(
                "Holo Dive binding could not be saved; retry attach before the Dive window expires"
            ) from exc
        self._holo_authorization.commit_attach(binding)
        LOGGER.info("holo_attached dive_session_id=%s", binding.dive_session_id)
        return binding

    def holo_snapshot_authorized(self) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        return self.holo_snapshot()

    def holo_skills_authorized(self) -> dict[str, object]:
        self._holo_authorization.require_attached()
        return self.skill_registry.public_payload()

    async def holo_wait_events_authorized(
        self,
        after_event_id: int,
        *,
        timeout_sec: float,
        limit: int = 50,
    ) -> HoloEventWaitResult:
        self._holo_authorization.require_attached()
        return await self.holo_wait_events(
            after_event_id,
            timeout_sec=timeout_sec,
            limit=limit,
        )

    async def holo_world_say_authorized(
        self,
        text: str,
        *,
        to: str | None = None,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        return await self.holo_world_say(text, to=to)

    def holo_snapshot(self) -> dict[str, Any]:
        """Return the allowlisted public state exposed to the local Holo Addon."""
        active_session = self.sessions.active_session_id
        return {
            "world_connected": self._world_connection is not None,
            "time_of_day": time_of_day(),
            "active_session": active_session,
            "residents": [
                {
                    "name": resident.name,
                    "location": resident.spawn_location,
                }
                for resident in self.resident_service.list_enabled()
            ],
            "recent_public_entries": self.sessions.public_history(active_session, limit=20),
            "latest_event_id": self._holo_events.latest_event_id,
        }

    async def holo_wait_events(
        self,
        after_event_id: int,
        *,
        timeout_sec: float,
        limit: int = 50,
    ) -> HoloEventWaitResult:
        """Wait for allowlisted semantic events after a known cursor."""
        return await self._holo_events.wait_after(
            after_event_id,
            timeout_sec=timeout_sec,
            limit=limit,
        )

    async def _publish_holo_public_entry(self, entry: dict[str, Any]) -> None:
        await self._holo_events.publish(
            "world.public_entry",
            {"entry": dict(entry)},
        )

    def _holo_resident_name(self) -> str:
        """Speaker name for Holo public entries: the holo-addon Resident if one
        exists, so the World avatar and chat log stay coherent."""
        for resident in self.resident_service.list_enabled():
            if resident.brain == HOLO_ADDON_BRAIN:
                return resident.name
        return "Holo"

    async def holo_world_say(
        self,
        text: str,
        *,
        to: str | None = None,
    ) -> dict[str, Any]:
        """Publish a Holo-authored entry to the public World conversation."""
        cleaned = text.strip()
        if not cleaned:
            raise ChatStoreError("Holo World Say text must not be empty")
        if to is not None:
            target = to.strip()
            if not target or target not in self.resident_service.enabled_names:
                raise ResidentError(f"Resident is not enabled: {to}")
            to = target

        entry = self.sessions.append_holo_say(cleaned, to=to, sender=self._holo_resident_name())
        self.world_memory.record_public_entry(entry)
        await self._publish_holo_public_entry(entry)

        websocket = self._world_connection
        if websocket is not None:
            try:
                await websocket.send(make_message("chat_append", {"entry": entry}))
                await self._send_session_list(websocket)
            except Exception:
                LOGGER.warning(
                    "holo_world_say_publish_failed session_id=%s to=%s",
                    entry.get("session"),
                    to,
                    exc_info=True,
                )
        LOGGER.info(
            "holo_world_say_saved session_id=%s to=%s",
            entry.get("session"),
            to,
        )
        return entry

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await serve(self._handle_connection, self.host, self.port)
        LOGGER.info("server_listening host=%s port=%s", self.host, self.bound_port)
        self._schedule_task_queue_dispatch()

    async def stop(self) -> None:
        if self._server is None:
            return
        await self.agent_runtime.begin_stop()
        dispatch_task = self._task_queue_dispatch_task
        if dispatch_task is not None and not dispatch_task.done():
            dispatch_task.cancel()
            await asyncio.gather(dispatch_task, return_exceptions=True)
        self._task_queue_dispatch_task = None
        await self._cancel_task_flow()
        LOGGER.info(
            "server_stop_start active_responses=%s active_resident_chats=%s",
            len(self._response_tasks),
            len(self._resident_chat_tasks),
        )
        await self._cancel_all_responses()
        await self._cancel_all_resident_chats()
        await self.agent_runtime.stop()
        for task in self._provider_catalog_tasks.values():
            if not task.done():
                task.cancel()
        if self._provider_catalog_tasks:
            await asyncio.gather(*self._provider_catalog_tasks.values(), return_exceptions=True)
        self._provider_catalog_tasks.clear()
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        LOGGER.info("server_stop_done")

    async def run_forever(self) -> None:
        await self.start()
        assert self._server is not None
        try:
            await self._server.serve_forever()
        finally:
            await self.stop()

    @staticmethod
    def _agent_task_phase_for_state(state: object) -> str | None:
        return {
            "running": "running",
            "completed": "done",
            "failed": "failed",
            "interrupted": "failed",
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
        detail = latest_error if isinstance(latest_error, str) and latest_error.strip() else "作業を完了できませんでした"
        return f"Task失敗: {detail}"

    def _restore_agent_task_state(self) -> None:
        for snapshot in self.agent_runtime.list_snapshots():
            origin_session_id = snapshot.origin_chat_session_id
            if not origin_session_id:
                continue
            self._agent_task_chat_sessions[snapshot.agent_session_id] = origin_session_id
            terminal_phase = (
                self._agent_task_phase_for_state(snapshot.run_state)
                if snapshot.run_state in TERMINAL_RUN_STATES
                else None
            )
            phase = snapshot.task_phase or terminal_phase or "assigned"
            self._agent_task_phases[snapshot.agent_session_id] = phase
            if snapshot.result_reported:
                self._agent_task_reported.add(snapshot.agent_session_id)
                if terminal_phase is not None and snapshot.task_phase != terminal_phase:
                    snapshot = self.agent_runtime.update_task_metadata(
                        snapshot.agent_session_id,
                        task_phase=terminal_phase,
                        result_reported=True,
                    )
                    self._agent_task_phases[snapshot.agent_session_id] = terminal_phase
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
                LOGGER.warning(
                    "agent_task_recovery_chat_missing task_id=%s agent_session_id=%s session_id=%s",
                    snapshot.task_id,
                    snapshot.agent_session_id,
                    origin_session_id,
                )
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
            self.world_memory.record_public_entry(chat_entry)
            self.agent_runtime.update_task_metadata(
                snapshot.agent_session_id,
                task_phase=terminal_phase,
                result_reported=True,
                result_notified=False,
            )
            self._agent_task_reported.add(snapshot.agent_session_id)
            self._agent_task_phases[snapshot.agent_session_id] = terminal_phase
            self._recovered_agent_notifications[snapshot.agent_session_id] = (
                {
                    "task_id": snapshot.task_id,
                    "phase": terminal_phase,
                    "text": text,
                    "agent_session_id": snapshot.agent_session_id,
                },
                chat_entry,
            )

    async def _broadcast_agent_event(self, event: AgentEvent) -> None:
        task_update, chat_entry = await self._handle_agent_task_event(event)
        terminal_update = (
            task_update is not None
            and task_update.get("phase") in {"done", "failed", "cancelled"}
        )
        if terminal_update:
            self._recovered_agent_notifications[event.agent_session_id] = (
                dict(task_update),
                chat_entry,
            )
            self._schedule_task_queue_dispatch()

        websocket = self._world_connection
        if websocket is None:
            return
        await websocket.send(make_message("agent_event", {"event": event.to_protocol()}))
        if chat_entry is not None:
            await websocket.send(make_message("chat_append", {"entry": chat_entry}))
            await self._send_session_list(websocket)
        if task_update is not None:
            await websocket.send(make_message("task_update", task_update))
            if terminal_update:
                self.agent_runtime.update_task_metadata(
                    event.agent_session_id,
                    result_notified=True,
                )
                self._recovered_agent_notifications.pop(event.agent_session_id, None)

    async def _handle_agent_task_event(
        self,
        event: AgentEvent,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        if event.type != "run_state":
            return None, None
        snapshot_payload = self.agent_runtime.snapshot_payload(event.agent_session_id)
        session_snapshot = snapshot_payload["session"]
        origin_session_id = (
            self._agent_task_chat_sessions.get(event.agent_session_id)
            or session_snapshot.get("origin_chat_session_id")
        )
        if not isinstance(origin_session_id, str) or not origin_session_id:
            return None, None
        self._agent_task_chat_sessions[event.agent_session_id] = origin_session_id

        phase = self._agent_task_phase_for_state(event.payload.get("state"))
        current_phase = (
            self._agent_task_phases.get(event.agent_session_id)
            or session_snapshot.get("task_phase")
        )
        if phase is None or current_phase == phase:
            return None, None

        chat_entry: dict[str, Any] | None = None
        result_persisted = False
        text = f"{event.resident}が作業中です"
        if phase in {"done", "failed", "cancelled"}:
            text = self._agent_task_result_text(event.resident, phase, snapshot_payload)
            already_reported = (
                event.agent_session_id in self._agent_task_reported
                or session_snapshot.get("result_reported") is True
            )
            if not already_reported:
                try:
                    chat_entry = self.sessions.find_task_entry(
                        origin_session_id,
                        event.agent_session_id,
                    )
                    if chat_entry is None:
                        chat_entry = self.sessions.append_task(
                            origin_session_id,
                            event.resident,
                            text,
                            task_id=event.task_id,
                            agent_session_id=event.agent_session_id,
                        )
                    self.world_memory.record_public_entry(chat_entry)
                    await self._publish_holo_public_entry(chat_entry)
                    result_persisted = True
                    self._agent_task_reported.add(event.agent_session_id)
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
                or event.agent_session_id in self._agent_task_reported
                or session_snapshot.get("result_reported") is True
            )
        )
        self.agent_runtime.update_task_metadata(
            event.agent_session_id,
            task_phase=phase,
            result_reported=metadata_reported if phase in {"done", "failed", "cancelled"} else None,
            result_notified=False if phase in {"done", "failed", "cancelled"} else None,
        )
        self._agent_task_phases[event.agent_session_id] = phase
        return {
            "task_id": event.task_id,
            "phase": phase,
            "text": text,
            "agent_session_id": event.agent_session_id,
            "working_dir": session_snapshot["working_dir"],
        }, chat_entry

    def _chat_session_has_active_agent_task(self, session_id: str) -> bool:
        for agent_session_id, origin_session_id in self._agent_task_chat_sessions.items():
            if origin_session_id != session_id:
                continue
            try:
                snapshot = self.agent_runtime.snapshot_payload(agent_session_id)["session"]
            except AgentRuntimeManagerError:
                continue
            if snapshot.get("run_state") not in TERMINAL_RUN_STATES:
                return True
        return False

    def _agent_snapshot_payload(self, agent_session_id: str) -> dict[str, Any]:
        payload = self.agent_runtime.snapshot_payload(agent_session_id)
        session = payload["session"]
        events = payload["events"]
        pending_input: dict[str, Any] | None = None
        pending_request_id = session.get("pending_request_id")
        if isinstance(pending_request_id, str) and pending_request_id:
            for event in reversed(events):
                event_payload = event.get("payload")
                if (
                    isinstance(event_payload, dict)
                    and event_payload.get("request_id") == pending_request_id
                    and event.get("type") in {"approval_request", "question_request", "plan"}
                ):
                    pending_input = {
                        "type": event["type"],
                        "request_id": pending_request_id,
                        "payload": dict(event_payload),
                    }
                    break
        return {
            "agent_session_id": session["agent_session_id"],
            "task_id": session["task_id"],
            "resident": session["resident"],
            "provider": session["provider"],
            "state": session["run_state"],
            "working_dir": session["working_dir"],
            "started_at": session["started_at"],
            "updated_at": session["updated_at"],
            "last_event_seq": session["last_event_seq"],
            "final_summary": session["final_summary"],
            "origin_chat_session_id": session.get("origin_chat_session_id"),
            "task_phase": session.get("task_phase"),
            "result_reported": session.get("result_reported") is True,
            "events": events,
            **({"pending_input": pending_input} if pending_input is not None else {}),
        }

    async def _send_agent_snapshot(
        self,
        websocket: ServerConnection,
        agent_session_id: str,
        message_id: str | None = None,
    ) -> None:
        await websocket.send(make_message(
            "agent_session_snapshot",
            self._agent_snapshot_payload(agent_session_id),
            message_id,
        ))

    async def _send_active_agent_snapshots(self, websocket: ServerConnection) -> None:
        for snapshot in self.agent_runtime.list_snapshots():
            if snapshot.run_state in TERMINAL_RUN_STATES:
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

    def _provider_can_agent_work(self, provider: str, model: str | None = None) -> bool:
        if not self._provider_is_available(provider) or not self.agent_runtime.supports_provider(provider):
            return False
        if provider == "gemini":
            return isinstance(model, str) and bool(model.strip()) and is_antigravity_model(model.strip())
        return True

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
                if record.target_name is not None:
                    named = self.agent_runtime.workspace_policy.named_working_dir(
                        record.target_name,
                        task_id=record.task_id,
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
                recovered.append(QueuedTaskRecord(
                    task_id=record.task_id,
                    text=record.text,
                    message_id=None,
                    origin_session_id=record.origin_session_id,
                    working_dir=str(working_dir),
                    task_metadata_dir=str(metadata_dir),
                    target_name=record.target_name,
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
                }
            if (
                state.active is not None
                and state.active.task_id not in durable_agent_task_ids
            ):
                LOGGER.warning(
                    "task_queue_recovered_active task_id=%s queued_for_retry=true",
                    state.active.task_id,
                )
        except (TaskQueueStoreError, AgentSafetyError, OSError, ValueError) as exc:
            self._task_queue = []
            self._active_pre_agent_task = None
            self._task_queue_store_error = str(exc)
            LOGGER.error(
                "task_queue_restore_failed error_type=%s error=%s",
                type(exc).__name__,
                str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n"),
            )

    def _persist_task_queue_state(self) -> None:
        if self._task_queue_store_error is not None:
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
            raise AgentRuntimeManagerError(
                f"Task Queue persistence failed: {exc}"
            ) from exc

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
        return (task is not None and not task.done()) or self.agent_runtime.has_active_session()

    def _task_work_pending(self) -> bool:
        return (
            self._task_flow_busy()
            or self._active_pre_agent_task is not None
            or bool(self._task_queue)
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
            else self.agent_runtime.workspace_policy.named_working_dir(target_name, task_id=task_id)
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
            or self._task_queue_store_error is not None
            or self._task_flow_busy()
            or self._active_pre_agent_task is not None
            or not self._task_queue
        ):
            return
        previous_queue = list(self._task_queue)
        request = previous_queue[0]
        self._active_pre_agent_task = request
        self._task_queue = previous_queue[1:]
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
            or self._task_queue_store_error is not None
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
                async with self._brain_call_lock:
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
                    entry = self.sessions.append_resident_chat(
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
    ) -> None:
        origin_session_id = origin_session_id or self.sessions.active_session_id
        try:
            await self._send_task_update(
                task_id,
                "consulting",
                "Residentたちが担当を相談しています",
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
                resolved_working_dir = self.agent_runtime.workspace_policy.named_working_dir(
                    target_name,
                    task_id=task_id,
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

            resident, participants = await self._consult_task_residents(
                task_id,
                text,
                origin_session_id,
            )
            if resident is None:
                if participants:
                    detail = "誰も手が挙がらなかったため、Taskを終了しました"
                else:
                    detail = "Task相談に参加できるResidentがいないため、Taskを終了しました"
                await self._send_task_update(task_id, "failed", detail)
                return

            resident = self.resident_service.load(resident.name)
            if resident.brain is None or not self._provider_can_agent_work(resident.brain, resident.brain_model):
                raise AgentRuntimeManagerError(
                    f"Selected Resident is no longer eligible for Agent work: {resident.name}"
                )
            if target_name is not None:
                latest_working_dir = self.agent_runtime.workspace_policy.named_working_dir(
                    target_name,
                    task_id=task_id,
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
            )
            self._agent_task_chat_sessions[snapshot.agent_session_id] = origin_session_id
            self._agent_task_phases[snapshot.agent_session_id] = "assigned"
            await self._send_task_update(
                task_id,
                "assigned",
                f"{resident.name}が最初の有資格立候補者として担当に決まりました",
                message_id=message_id,
                agent_session_id=snapshot.agent_session_id,
                working_dir=snapshot.working_dir,
                extra={
                    "assigned_resident": resident.name,
                    "assignment_policy": "first_eligible_volunteer",
                },
            )
        except asyncio.CancelledError:
            await self._send_task_update(
                task_id,
                "cancelled",
                "Task相談を停止しました",
                message_id=message_id,
            )
            raise
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

    async def _cancel_task_flow(self) -> None:
        task = self._task_flow_task
        if task is None or task.done():
            return
        for invocation_id in tuple(self._task_consult_invocations):
            driver = self._invocation_drivers.get(invocation_id)
            if driver is not None:
                try:
                    await asyncio.wait_for(
                        driver.cancel(invocation_id),
                        timeout=TASK_CONSULT_CANCEL_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    LOGGER.warning(
                        "task_consult_cancel_timeout invocation_id=%s timeout_sec=%s",
                        invocation_id,
                        TASK_CONSULT_CANCEL_TIMEOUT_SEC,
                    )
                except Exception:
                    LOGGER.warning(
                        "task_consult_cancel_failed invocation_id=%s",
                        invocation_id,
                        exc_info=True,
                    )
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def _send_holo_local_result(
        self,
        websocket: ServerConnection,
        message_id: str | None,
        operation: str,
        payload: dict[str, Any],
    ) -> None:
        await websocket.send(make_message(
            "holo_local_result",
            {"operation": operation, **payload},
            message_id,
        ))

    async def _handle_holo_local_message(
        self,
        websocket: ServerConnection,
        message: dict[str, Any],
    ) -> None:
        message_type = message["type"]
        payload = message["payload"]
        message_id = message.get("id")
        try:
            if message_type == "holo_attach_request":
                binding = self.holo_attach()
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "attach",
                    {"ok": True, "dive_session_id": binding.dive_session_id},
                )
                await self._send_holo_addon_state()
                return
            if message_type == "holo_snapshot_request":
                snapshot = self.holo_snapshot_authorized()
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "snapshot",
                    {"ok": True, "snapshot": snapshot},
                )
                return
            if message_type == "holo_skills_request":
                skills = self.holo_skills_authorized()
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "skills",
                    {"ok": True, **skills},
                )
                return
            if message_type == "holo_world_say_request":
                text = payload.get("text")
                to = payload.get("to")
                if not isinstance(text, str):
                    raise ChatStoreError("Holo World Say text must be a string")
                if to is not None and not isinstance(to, str):
                    raise ResidentError("Holo World Say target must be a Resident name")
                entry = await self.holo_world_say_authorized(text, to=to)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "world_say",
                    {"ok": True, "entry": entry},
                )
                return
            if message_type == "holo_wait_events_request":
                after_event_id = payload.get("after_event_id")
                timeout_sec = payload.get("timeout_sec")
                limit = payload.get("limit", 50)
                if not isinstance(after_event_id, int) or isinstance(after_event_id, bool):
                    raise ValueError("after_event_id must be an integer")
                if not isinstance(timeout_sec, (int, float)) or isinstance(timeout_sec, bool):
                    raise ValueError("timeout_sec must be a number")
                if not isinstance(limit, int) or isinstance(limit, bool):
                    raise ValueError("limit must be an integer")

                wait_task = asyncio.create_task(
                    self.holo_wait_events_authorized(
                        after_event_id,
                        timeout_sec=float(timeout_sec),
                        limit=limit,
                    )
                )
                closed_task = asyncio.create_task(websocket.wait_closed())
                done, _ = await asyncio.wait(
                    {wait_task, closed_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if closed_task in done and wait_task not in done:
                    wait_task.cancel()
                    await asyncio.gather(wait_task, return_exceptions=True)
                    return
                closed_task.cancel()
                await asyncio.gather(closed_task, return_exceptions=True)
                result = await wait_task
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "wait_events",
                    {
                        "ok": True,
                        "events": list(result.events),
                        "latest_event_id": result.latest_event_id,
                        "timed_out": result.timed_out,
                    },
                )
                return
            raise HoloAuthorizationError("Unsupported Holo local operation")
        except (
            ChatStoreError,
            HoloAuthorizationError,
            ResidentError,
            ValueError,
        ) as exc:
            await websocket.send(make_message(
                "holo_local_result",
                {
                    "operation": message_type,
                    "ok": False,
                    "error": str(exc),
                },
                message_id,
            ))
            if message_type == "holo_attach_request":
                try:
                    # Reassert the observable state after a failed durable
                    # commit. World must remain attach_waiting, never attached.
                    await self._send_holo_addon_state()
                except Exception:
                    LOGGER.warning("holo_attach_failure_state_publish_failed", exc_info=True)

    async def _handle_connection(self, websocket: ServerConnection) -> None:
        holo_local_authenticated = False
        try:
            async for raw_message in websocket:
                if not isinstance(raw_message, str):
                    continue
                try:
                    message = parse_message(raw_message)
                except ProtocolError as exc:
                    LOGGER.warning("protocol_invalid error=%s", exc)
                    continue

                message_type = message["type"]
                payload = message["payload"]
                LOGGER.debug("protocol_received type=%s", message_type)

                if message_type == "hello":
                    role = payload.get("role")
                    if role == "holo_local":
                        secret = payload.get("secret")
                        expected = self._holo_local_secret
                        if (
                            expected is None
                            or not isinstance(secret, str)
                            or not hmac.compare_digest(secret, expected)
                        ):
                            LOGGER.warning("holo_local_auth_rejected")
                            await websocket.close(code=4003, reason="Holo local authentication failed")
                            return
                        holo_local_authenticated = True
                        await websocket.send(make_message(
                            "holo_local_hello_ack",
                            {"ok": True},
                            message.get("id"),
                        ))
                        continue
                    if role != "world":
                        continue
                    secret = payload.get("secret")
                    if (
                        not isinstance(secret, str)
                        or not hmac.compare_digest(secret, self._world_secret)
                    ):
                        LOGGER.warning("world_auth_rejected")
                        await websocket.close(code=4003, reason="World authentication failed")
                        return
                    previous = self._world_connection
                    if previous is not None and previous is not websocket:
                        await previous.close(code=4000, reason="replaced by newer world connection")
                    self._world_connection = websocket
                    LOGGER.info("world_connected")
                    await self._holo_events.publish("world.connection", {"connected": True})
                    await self._send_hello_ack(websocket, message.get("id"))
                    await self._send_active_agent_snapshots(websocket)
                    if self._recovered_agent_notifications:
                        await self._send_recovered_agent_notifications(websocket)
                    if self._pending_pre_agent_task_updates:
                        await self._send_pending_pre_agent_task_updates(websocket)
                    continue

                if holo_local_authenticated:
                    await self._handle_holo_local_message(websocket, message)
                    continue

                if self._world_connection is not websocket:
                    LOGGER.warning("protocol_ignored_unregistered_world type=%s", message_type)
                    continue

                try:
                    if message_type == "action_done":
                        action_id = message.get("id")
                        if isinstance(action_id, str):
                            waiter = self._action_waiters.pop(action_id, None)
                            if waiter is not None and not waiter.done():
                                waiter.set_result(dict(payload))
                        continue
                    if message_type == "holo_dive_started":
                        dive_session_id = payload.get("dive_session_id")
                        attach_expires_at_ms = payload.get("attach_expires_at_ms")
                        if not isinstance(dive_session_id, str) or not dive_session_id.strip():
                            continue
                        if (
                            not isinstance(attach_expires_at_ms, (int, float))
                            or isinstance(attach_expires_at_ms, bool)
                        ):
                            LOGGER.warning("holo_dive_started_missing_deadline")
                            await self._send_holo_addon_state(websocket, message.get("id"))
                            continue
                        try:
                            self.holo_open_attach_window(
                                dive_session_id,
                                attach_expires_at_ms=float(attach_expires_at_ms),
                            )
                        except (HoloAuthorizationError, ValueError):
                            LOGGER.warning(
                                "holo_dive_started_expired_or_invalid dive_session_id=%s",
                                dive_session_id,
                            )
                        # Echo the request id so World can distinguish an
                        # acknowledged Dive transition from an unsolicited
                        # lifecycle refresh or a stale attached binding.
                        await self._send_holo_addon_state(websocket, message.get("id"))
                        continue
                    if message_type == "holo_addon_state_request":
                        await self._send_holo_addon_state(websocket, message.get("id"))
                        continue
                    if message_type == "brain_provider_list_request":
                        await websocket.send(
                            make_message(
                                "brain_provider_list",
                                {"providers": self._brain_provider_list()},
                                message.get("id"),
                            )
                        )
                        self._schedule_provider_catalog_refresh(websocket)
                    elif message_type == "chat_session_list_request":
                        await self._send_session_list(websocket, message.get("id"))
                    elif message_type == "chat_session_create":
                        session = self.sessions.create_session()
                        LOGGER.info("session_created session_id=%s", session["id"])
                        await self._send_session_list(websocket, message.get("id"))
                        await self._send_history(websocket, self.sessions.active_session_id)
                    elif message_type == "chat_session_select":
                        session_id = payload.get("session_id")
                        if isinstance(session_id, str):
                            self.sessions.select_session(session_id)
                            LOGGER.info("session_selected session_id=%s", session_id)
                            await self._send_session_list(websocket, message.get("id"))
                            await self._send_history(websocket, session_id)
                    elif message_type == "chat_session_delete":
                        session_id = payload.get("session_id")
                        if not isinstance(session_id, str):
                            continue
                        if any(not task.done() for task in self._response_tasks.values()):
                            raise ChatStoreError("AI応答中はチャット履歴を削除できません")
                        if self._chat_session_has_active_task(session_id):
                            raise ChatStoreError("Task相談またはAgent作業中のチャット履歴は削除できません")
                        active_session = self.sessions.delete_session(session_id)
                        LOGGER.info(
                            "session_deleted session_id=%s active_session=%s",
                            session_id,
                            active_session,
                        )
                        await self._send_session_list(websocket, message.get("id"))
                        await self._send_history(websocket, active_session)
                    elif message_type == "world_memory_forget_session":
                        session_id = payload.get("session_id")
                        if not isinstance(session_id, str):
                            continue
                        if any(not task.done() for task in self._response_tasks.values()):
                            raise ChatStoreError("AI応答中は世界の記憶を変更できません")
                        if self._chat_session_has_active_task(session_id):
                            raise ChatStoreError("Task相談またはAgent作業中のWorld Memoryは変更できません")
                        if not self.sessions.store.has_session(session_id):
                            raise ChatStoreError(f"unknown chat session: {session_id}")
                        deleted_count = self.world_memory.forget_session(session_id)
                        active_session = self.sessions.delete_session(session_id)
                        LOGGER.info(
                            "world_memory_forgotten session_id=%s episode_count=%s chat_history_deleted=true active_session=%s",
                            session_id,
                            deleted_count,
                            active_session,
                        )
                        await self._send_session_list(websocket, message.get("id"))
                        await self._send_history(websocket, active_session)
                    elif message_type == "history_request":
                        session_id = payload.get("session_id")
                        before = payload.get("before")
                        limit = payload.get("limit", 50)
                        if not isinstance(session_id, str):
                            session_id = self.sessions.active_session_id
                        if not isinstance(before, str):
                            before = None
                        if not isinstance(limit, int) or isinstance(limit, bool):
                            limit = 50
                        await self._send_history(
                            websocket,
                            session_id,
                            before=before,
                            limit=limit,
                            message_id=message.get("id"),
                        )
                    elif message_type == "master_say":
                        text = payload.get("text")
                        request_id = payload.get("request_id")
                        if not isinstance(text, str) or not text.strip():
                            continue
                        if not isinstance(request_id, str) or not request_id:
                            continue
                        entry = self.sessions.append_master_say(text, request_id)
                        self.world_memory.record_public_entry(entry)
                        await self._publish_holo_public_entry(entry)
                        LOGGER.info(
                            "master_say_saved request_id=%s session_id=%s",
                            request_id,
                            entry["session"],
                        )
                        await websocket.send(make_message("chat_append", {"entry": entry}))
                        await self._send_session_list(websocket)
                        task = asyncio.create_task(
                            self._respond_to_master(websocket, request_id, entry["session"])
                        )
                        self._response_tasks[request_id] = task
                        task.add_done_callback(
                            lambda finished, current_request_id=request_id: self._response_task_done(
                                current_request_id,
                                finished,
                            )
                        )
                    elif message_type == "master_whisper":
                        text = payload.get("text")
                        request_id = payload.get("request_id")
                        resident_name = payload.get("to")
                        if not isinstance(text, str) or not text.strip():
                            continue
                        if not isinstance(request_id, str) or not request_id:
                            continue
                        if not isinstance(resident_name, str) or not resident_name:
                            continue
                        whisper_target = self.resident_service.load(resident_name)
                        if whisper_target.brain == HOLO_ADDON_BRAIN:
                            # The Holo private conversation lives in ChatGPT.
                            # Do not store a parallel history on the Nirai side.
                            await websocket.send(
                                make_message(
                                    "notice",
                                    {
                                        "level": "WARN",
                                        "text": f"{resident_name}との個別会話はHolo Whisper（ChatGPT）で行います",
                                    },
                                )
                            )
                            continue
                        entry = self.sessions.append_master_whisper(resident_name, text, request_id)
                        self.private_memory.append_whisper(
                            resident_name,
                            session_id=entry["session"],
                            sender="master",
                            recipient=resident_name,
                            text=entry["text"],
                            request_id=request_id,
                            ts=entry["ts"],
                        )
                        LOGGER.info(
                            "master_whisper_saved request_id=%s session_id=%s resident=%s",
                            request_id,
                            entry["session"],
                            resident_name,
                        )
                        await websocket.send(make_message("chat_append", {"entry": entry}))
                        await self._send_session_list(websocket)
                        task = asyncio.create_task(
                            self._respond_to_whisper(
                                websocket,
                                request_id,
                                entry["session"],
                                resident_name,
                            )
                        )
                        self._response_tasks[request_id] = task
                        task.add_done_callback(
                            lambda finished, current_request_id=request_id: self._response_task_done(
                                current_request_id,
                                finished,
                            )
                        )
                    elif message_type == "cancel_response":
                        request_id = payload.get("request_id")
                        if isinstance(request_id, str) and request_id:
                            await self._cancel_response(websocket, request_id)
                    elif message_type == "task_request":
                        text = payload.get("text")
                        if not isinstance(text, str) or not text.strip():
                            raise AgentRuntimeManagerError("Task request text must not be empty")
                        if len(text.strip()) > TASK_QUEUE_TEXT_LIMIT:
                            raise AgentRuntimeManagerError(
                                f"Task request text exceeds the {TASK_QUEUE_TEXT_LIMIT} character limit"
                            )
                        target = payload.get("target")
                        if target is not None and (not isinstance(target, str) or not target.strip()):
                            raise AgentRuntimeManagerError("Task target folder name must be a non-empty string")
                        target_name = target.strip() if isinstance(target, str) else None
                        if self.agent_runtime.is_stopping():
                            raise AgentRuntimeManagerError(
                                "Agent Runtime is stopping; new Task execution is not available"
                            )
                        if self._task_queue_store_error is not None:
                            raise AgentRuntimeManagerError(
                                f"Task Queue persistence is unavailable: {self._task_queue_store_error}"
                            )
                        should_queue = self._task_work_pending()
                        if should_queue and len(self._task_queue) >= TASK_QUEUE_PENDING_LIMIT:
                            raise AgentRuntimeManagerError(
                                f"Task Queue is full; maximum pending Tasks is {TASK_QUEUE_PENDING_LIMIT}"
                            )
                        task_id = f"T-{uuid4()}"
                        origin_session_id = self.sessions.active_session_id
                        working_dir, task_metadata_dir = self._prepare_task_request_paths(
                            task_id,
                            text,
                            target_name,
                        )
                        request = QueuedTaskRecord(
                            task_id=task_id,
                            text=text.strip(),
                            message_id=message.get("id"),
                            origin_session_id=origin_session_id,
                            working_dir=working_dir,
                            task_metadata_dir=task_metadata_dir,
                            target_name=target_name,
                        )
                        if should_queue:
                            queue_position = self._enqueue_task_record(request)
                            await self._send_task_update(
                                task_id,
                                "queued",
                                f"Taskを順番待ちに追加しました（{queue_position}番目）",
                                message_id=message.get("id"),
                                working_dir=working_dir,
                                extra={
                                    "queue_position": queue_position,
                                    **({"target": target_name} if target_name is not None else {}),
                                },
                            )
                            self._schedule_task_queue_dispatch()
                        else:
                            self._activate_task_record(request)
                            self._start_task_flow(request)
                    elif message_type == "agent_approval_response":
                        agent_session_id = payload.get("agent_session_id")
                        request_id = payload.get("request_id")
                        decision = payload.get("decision")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        if not isinstance(request_id, str) or not request_id:
                            raise AgentRuntimeManagerError("request_id is required")
                        if decision not in {"approve_once", "approve_session", "reject", "cancel"}:
                            raise AgentRuntimeManagerError("Agent approval decision is invalid")
                        accepted = await self.agent_runtime.respond(
                            agent_session_id,
                            request_id,
                            "approval",
                            {"decision": decision},
                        )
                        if not accepted:
                            raise AgentRuntimeManagerError("Agent approval request is no longer pending")
                    elif message_type == "agent_question_response":
                        agent_session_id = payload.get("agent_session_id")
                        request_id = payload.get("request_id")
                        answers = payload.get("answers")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        if not isinstance(request_id, str) or not request_id:
                            raise AgentRuntimeManagerError("request_id is required")
                        if not isinstance(answers, dict):
                            raise AgentRuntimeManagerError("Agent question answers must be an object")
                        accepted = await self.agent_runtime.respond(
                            agent_session_id,
                            request_id,
                            "question",
                            {"answers": dict(answers)},
                        )
                        if not accepted:
                            raise AgentRuntimeManagerError("Agent question request is no longer pending")
                    elif message_type == "agent_plan_response":
                        agent_session_id = payload.get("agent_session_id")
                        request_id = payload.get("request_id")
                        decision = payload.get("decision")
                        reason = payload.get("reason")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        if not isinstance(request_id, str) or not request_id:
                            raise AgentRuntimeManagerError("request_id is required")
                        if decision not in {"approve", "revise", "cancel"}:
                            raise AgentRuntimeManagerError("Agent plan decision is invalid")
                        if reason is not None and not isinstance(reason, str):
                            raise AgentRuntimeManagerError("Agent plan reason must be a string")
                        response = {"decision": decision}
                        if isinstance(reason, str) and reason:
                            response["reason"] = reason
                        accepted = await self.agent_runtime.respond(
                            agent_session_id,
                            request_id,
                            "plan",
                            response,
                        )
                        if not accepted:
                            raise AgentRuntimeManagerError("Agent plan request is no longer pending")
                    elif message_type == "agent_session_cancel":
                        agent_session_id = payload.get("agent_session_id")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        await self.agent_runtime.cancel(agent_session_id)
                        await self._send_agent_snapshot(websocket, agent_session_id, message.get("id"))
                    elif message_type == "agent_session_snapshot_request":
                        agent_session_id = payload.get("agent_session_id")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        await self._send_agent_snapshot(websocket, agent_session_id, message.get("id"))
                    elif message_type == "resident_create":
                        name = payload.get("name")
                        provider = payload.get("provider")
                        if not isinstance(name, str):
                            continue
                        if not isinstance(provider, str) or not provider.strip():
                            raise ResidentError("Resident作成にはAI選択が必要です")
                        if not self._provider_is_available(provider):
                            raise ResidentError(f"AI Providerを利用できません: {provider}")
                        model = payload.get("model")
                        if model is not None and not isinstance(model, str):
                            raise ResidentError("AI Model名が不正です")
                        reasoning_effort = payload.get("reasoning_effort")
                        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                            raise ResidentError("AI推論強度が不正です")
                        resident = self.resident_service.create(
                            name,
                            provider,
                            model,
                            reasoning_effort,
                        )
                        LOGGER.info(
                            "resident_create_applied name=%s provider=%s avatar=%s",
                            resident.name,
                            resident.brain,
                            resident.avatar,
                        )
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": resident.to_protocol()},
                                message.get("id"),
                            )
                        )
                    elif message_type == "resident_set_brain":
                        if self._task_work_pending():
                            raise ResidentError("Task相談・順番待ち・Agent作業中はResidentのAIを変更できません")
                        name = payload.get("name")
                        provider = payload.get("provider")
                        if not isinstance(name, str):
                            continue
                        if not isinstance(provider, str) or not provider.strip():
                            raise ResidentError("変更先のAIを選択してください")
                        if not self._provider_is_available(provider):
                            raise ResidentError(f"AI Providerを利用できません: {provider}")
                        model = payload.get("model")
                        if model is not None and not isinstance(model, str):
                            raise ResidentError("AI Model名が不正です")
                        reasoning_effort = payload.get("reasoning_effort")
                        if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                            raise ResidentError("AI推論強度が不正です")
                        resident = self.resident_service.set_brain(
                            name,
                            provider,
                            model,
                            reasoning_effort,
                        )
                        LOGGER.info(
                            "resident_set_brain_applied name=%s provider=%s model=%s reasoning=%s",
                            name,
                            provider,
                            resident.brain_model,
                            resident.brain_reasoning_effort,
                        )
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": resident.to_protocol()},
                                message.get("id"),
                            )
                        )
                    elif message_type == "resident_reorder":
                        names = payload.get("names")
                        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
                            raise ResidentError("Resident並び順が不正です")
                        self.resident_service.reorder(names)
                        LOGGER.info("resident_reorder_applied names=%s", ",".join(names))
                        await websocket.send(
                            make_message(
                                "resident_roster_updated",
                                {"residents": [resident.to_protocol() for resident in self.resident_service.list_enabled()]},
                                message.get("id"),
                            )
                        )
                    elif message_type == "resident_set_avatar":
                        name = payload.get("name")
                        avatar_path = payload.get("avatar_path")
                        if not isinstance(name, str) or not isinstance(avatar_path, str):
                            continue
                        resident = self.resident_service.set_avatar(name, avatar_path)
                        LOGGER.info("resident_set_avatar_applied name=%s", resident.name)
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": resident.to_protocol()},
                                message.get("id"),
                            )
                        )
                    elif message_type == "resident_delete":
                        if self._task_work_pending():
                            raise ResidentError("Task相談・順番待ち・Agent作業中はResidentを削除できません")
                        name = payload.get("name")
                        confirm = payload.get("confirm")
                        if not isinstance(name, str) or not isinstance(confirm, str):
                            continue
                        if any(not task.done() for task in self._response_tasks.values()):
                            raise ResidentError("AI応答中はResidentを削除できません")
                        self.resident_service.delete(name, confirm)
                        LOGGER.info("resident_delete_applied name=%s", name)
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": None, "deleted_name": name},
                                message.get("id"),
                            )
                        )
                    elif message_type == "resident_set_tts":
                        name = payload.get("name")
                        if not isinstance(name, str):
                            continue
                        resident = self.resident_service.set_tts(name, payload.get("tts"))
                        LOGGER.info("resident_set_tts_applied name=%s", resident.name)
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": resident.to_protocol()},
                                message.get("id"),
                            )
                        )
                    elif message_type == "audio_volume_changed":
                        volume = payload.get("volume")
                        if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
                            continue
                        save_audio_volume(self.config.root, volume)
                        self.audio_volume = volume
                        LOGGER.info("audio_volume_saved volume=%s", volume)
                    else:
                        LOGGER.warning("protocol_unknown type=%s", message_type)
                except (
                    AgentRuntimeManagerError,
                    AgentSafetyError,
                    AgentSessionStoreError,
                    ChatStoreError,
                    ResidentError,
                    PrivateMemoryError,
                    WorldMemoryError,
                    ConfigError,
                ) as exc:
                    LOGGER.warning(
                        "request_rejected type=%s error_type=%s error=%s",
                        message_type,
                        type(exc).__name__,
                        exc,
                    )
                    await websocket.send(
                        make_message(
                            "notice",
                            {"level": "WARN", "text": str(exc)},
                            message.get("id"),
                        )
                    )
        except Exception:
            LOGGER.exception("world_connection_error")
            raise
        finally:
            if self._world_connection is websocket:
                self._world_connection = None
                for action_id, waiter in list(self._action_waiters.items()):
                    if not waiter.done():
                        # Mark the presentation action as disconnected without
                        # cancelling the Future itself. Task consultation may
                        # tolerate this and continue; existing resident_chat keeps
                        # its prior cancellation semantics in _request_world_action.
                        waiter.set_result({
                            "ok": False,
                            "world_disconnected": True,
                            "reason": "World disconnected",
                        })
                    self._action_waiters.pop(action_id, None)
                LOGGER.info("world_disconnected")
                await self._holo_events.publish("world.connection", {"connected": False})

    def _brain_provider_list(self) -> list[dict[str, object]]:
        codex_default_model, codex_default_reasoning = load_codex_defaults()
        providers = (
            ("codex", "Codex", "subscription-cli", codex_default_model),
            ("claude-code", "Claude", "subscription-cli", None),
            ("cursor", "Cursor", "subscription-cli", "auto"),
            ("gemini", "Gemini", "api-key", GEMINI_DEFAULT_MODEL),
            (HOLO_ADDON_BRAIN, "Holo Addon", "addon", None),
            ("local-llm", "Local LLM", "local", None),
        )
        result: list[dict[str, object]] = []
        for name, display_name, configuration_mode, default_model in providers:
            available = self._provider_is_available(name)
            models: list[dict[str, Any]] = []
            if name == "claude-code":
                models = [
                    {"id": "opus", "display_name": "Opus (latest alias)"},
                    {"id": "sonnet", "display_name": "Sonnet (latest alias)"},
                    {"id": "fable", "display_name": "Fable (latest alias)"},
                ]
            elif name in {"codex", "cursor", "gemini"} and available:
                models = list(self._provider_models_cache.get(name, ()))
            # Provider-level capability describes the provider default model.
            # Providers such as Gemini are model-dependent, so a normal Gemini
            # default must not advertise Agent Work merely because an
            # Antigravity adapter is installed. Each listed model also carries
            # its own effective capability below.
            agent_work = self._provider_can_agent_work(name, default_model)
            agent_capabilities = self.agent_runtime.provider_capabilities(name) if agent_work else frozenset()
            if name == "gemini":
                models = [
                    {
                        **model,
                        "capabilities": self._agent_capabilities_payload(name, model.get("id")),
                    }
                    for model in models
                ]
            result.append({
                "name": name,
                "display_name": display_name,
                "available": available,
                "connected": available,
                "configuration_mode": configuration_mode if available else "unavailable",
                "models": models,
                "default_model": default_model,
                "default_reasoning_effort": codex_default_reasoning if name == "codex" else None,
                "custom_model_allowed": name in {"codex", "claude-code", "cursor", "gemini"},
                "capabilities": {
                    "conversation": available,
                    "agent_work": agent_work,
                    "approval": "approval" in agent_capabilities,
                    "question": "question" in agent_capabilities,
                    "plan": "plan" in agent_capabilities,
                    "todo": "todo" in agent_capabilities,
                    "subagent": "subagent" in agent_capabilities,
                    "file_diff": "file_diff" in agent_capabilities,
                    "command_result": "command_result" in agent_capabilities,
                    "artifact": "artifact" in agent_capabilities,
                },
            })
        return result

    def _agent_capabilities_payload(self, provider: str, model: object = None) -> dict[str, bool]:
        model_value = model if isinstance(model, str) else None
        agent_work = self._provider_can_agent_work(provider, model_value)
        capabilities = self.agent_runtime.provider_capabilities(provider) if agent_work else frozenset()
        return {
            "agent_work": agent_work,
            "approval": "approval" in capabilities,
            "question": "question" in capabilities,
            "plan": "plan" in capabilities,
            "todo": "todo" in capabilities,
            "subagent": "subagent" in capabilities,
            "file_diff": "file_diff" in capabilities,
            "command_result": "command_result" in capabilities,
            "artifact": "artifact" in capabilities,
        }

    def _schedule_provider_catalog_refresh(self, websocket: ServerConnection | None) -> None:
        loaders: tuple[tuple[str, Any], ...] = (
            ("codex", list_codex_models),
            ("cursor", lambda: list_cursor_models(self.config.root)),
            ("gemini", lambda: list_gemini_models(self.config.root)),
        )
        for provider, loader in loaders:
            if not self._provider_is_available(provider):
                continue
            task = self._provider_catalog_tasks.get(provider)
            if task is not None and not task.done():
                continue
            task = asyncio.create_task(
                self._refresh_provider_catalog(provider, loader, websocket),
                name=f"provider-catalog-{provider}",
            )
            self._provider_catalog_tasks[provider] = task
            task.add_done_callback(
                lambda finished, current_provider=provider: self._provider_catalog_tasks.pop(
                    current_provider,
                    None,
                ) if self._provider_catalog_tasks.get(current_provider) is finished else None
            )

    async def _refresh_provider_catalog(
        self,
        provider: str,
        loader: Any,
        websocket: ServerConnection | None,
    ) -> None:
        try:
            models = await asyncio.to_thread(loader)
        except asyncio.CancelledError:
            raise
        except BrainError as exc:
            LOGGER.warning("%s_model_catalog_unavailable error=%s", provider, str(exc)[:500])
            return
        except Exception as exc:
            LOGGER.warning(
                "%s_model_catalog_failed error_type=%s error=%s",
                provider,
                type(exc).__name__,
                str(exc)[:500],
            )
            return

        self._provider_models_cache[provider] = models
        target = websocket if websocket is self._world_connection else self._world_connection
        if target is None:
            return
        try:
            await target.send(
                make_message(
                    "brain_provider_list",
                    {"providers": self._brain_provider_list()},
                )
            )
        except Exception:
            LOGGER.debug("provider_catalog_push_skipped provider=%s", provider, exc_info=True)

    def _provider_is_available(self, provider: str) -> bool:
        try:
            if provider == HOLO_ADDON_BRAIN:
                # The Holo Addon ships with World itself; no CLI or key needed.
                return True
            if provider == "codex":
                resolve_codex_command()
                return True
            if provider == "cursor":
                resolve_cursor_command()
                return True
            if provider == "claude-code":
                # Driver implementation exists, but the 2026-08-29 live smoke
                # returned HTTP 403 because subscription access is unavailable.
                # Keep it unselectable until a future live smoke succeeds.
                return False
            if provider == "gemini":
                return load_gemini_api_key(self.config.root) is not None
        except BrainUnavailableError:
            return False
        return False

    def _get_brain_driver(self, provider: str) -> BrainDriver:
        if provider == HOLO_ADDON_BRAIN:
            # Defensive boundary: the Holo mind is the ChatGPT Web conversation
            # and must never be driven through the normal Brain Driver path.
            raise BrainError("Holo AddonはBrain Driverを使用しません")
        if self._brain_driver_override is not None:
            return self._brain_driver_override
        existing = self._brain_drivers.get(provider)
        if existing is not None:
            return existing

        if provider == "codex":
            driver: BrainDriver = CodexDriver(self.config.root)
        elif provider == "claude-code":
            driver = ClaudeCodeDriver(self.config.root)
        elif provider == "cursor":
            driver = CursorDriver(self.config.root)
        elif provider == "gemini":
            driver = GeminiDriver(self.config.root)
        else:
            raise BrainError(f"Brain provider is not implemented yet: {provider}")
        self._brain_drivers[provider] = driver
        return driver

    def _response_task_done(
        self,
        request_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._response_tasks.get(request_id) is task:
            self._response_tasks.pop(request_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "response_task_failed request_id=%s",
                request_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def _cancel_response(
        self,
        websocket: ServerConnection,
        request_id: str,
    ) -> None:
        task = self._response_tasks.get(request_id)
        if task is None or task.done():
            LOGGER.info("cancel_no_active_response request_id=%s", request_id)
            await websocket.send(
                make_message(
                    "response_state",
                    {
                        "active": False,
                        "request_id": request_id,
                        "session_id": self.sessions.active_session_id,
                    },
                )
            )
            return

        self._cancelled_requests.add(request_id)
        invocation_ids = tuple(self._request_invocations.get(request_id, ()))
        LOGGER.info(
            "cancel_requested request_id=%s invocation_count=%s",
            request_id,
            len(invocation_ids),
        )
        for invocation_id in invocation_ids:
            driver = self._invocation_drivers.get(invocation_id)
            if driver is None:
                continue
            cancelled = await driver.cancel(invocation_id)
            LOGGER.info(
                "cancel_invocation request_id=%s invocation_id=%s cancelled=%s",
                request_id,
                invocation_id,
                cancelled,
            )

    async def _cancel_all_responses(self) -> None:
        if not self._response_tasks:
            return

        self._cancelled_requests.update(self._response_tasks)
        invocation_ids = {
            invocation_id
            for ids in self._request_invocations.values()
            for invocation_id in ids
        }
        for invocation_id in invocation_ids:
            driver = self._invocation_drivers.get(invocation_id)
            if driver is not None:
                await driver.cancel(invocation_id)

        tasks = list(self._response_tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def _world_memory_context(
        self,
        query: str,
        *,
        recent_public_entries: list[dict[str, Any]],
    ) -> list[dict[str, object]]:
        if not query.strip():
            return []
        excluded_markers = {
            WorldMemoryService.entry_marker(entry)
            for entry in recent_public_entries
        }
        try:
            hits = self.world_retriever.search(
                query,
                top_k=WorldMemoryRetriever.DEFAULT_TOP_K,
                exclude_entry_markers=excluded_markers,
            )
        except WorldMemoryRetrieverError:
            LOGGER.warning("world_memory_retrieval_failed", exc_info=True)
            return []
        return [hit.to_context() for hit in hits]

    async def _respond_to_master(
        self,
        websocket: ServerConnection,
        request_id: str,
        session_id: str,
    ) -> None:
        await websocket.send(
            make_message(
                "response_state",
                {
                    "active": True,
                    "request_id": request_id,
                    "session_id": session_id,
                },
            )
        )

        current_public_history = self.sessions.public_history(session_id, limit=20)
        memory_query = current_public_history[-1].get("text", "") if current_public_history else ""
        world_memories = self._world_memory_context(
            memory_query if isinstance(memory_query, str) else "",
            recent_public_entries=current_public_history,
        )

        residents = [
            resident
            for resident in self.resident_service.list_enabled()
            # Holo hears the world through its own snapshot/wait channel; the
            # public Say loop must not push it through a Brain Driver.
            if resident.brain is not None and resident.brain != HOLO_ADDON_BRAIN
        ]
        if not residents:
            LOGGER.info("brain_skipped_no_configured_resident request_id=%s session_id=%s", request_id, session_id)
            await websocket.send(
                make_message(
                    "response_state",
                    {
                        "active": False,
                        "request_id": request_id,
                        "session_id": session_id,
                    },
                )
            )
            return

        try:
            for resident in residents:
                if request_id in self._cancelled_requests:
                    LOGGER.info(
                        "brain_queue_cancelled_before_start request_id=%s resident=%s",
                        request_id,
                        resident.name,
                    )
                    break

                assert resident.brain is not None
                invocation_id = f"INV-{uuid4()}"
                self._request_invocations.setdefault(request_id, set()).add(invocation_id)
                started_at = perf_counter()
                LOGGER.info(
                    "brain_start request_id=%s invocation_id=%s session_id=%s resident=%s provider=%s mode=talk",
                    request_id,
                    invocation_id,
                    session_id,
                    resident.name,
                    resident.brain,
                )
                try:
                    driver = self._get_brain_driver(resident.brain)
                    self._invocation_drivers[invocation_id] = driver
                    if request_id in self._cancelled_requests:
                        break
                    async with self._brain_call_lock:
                        if request_id in self._cancelled_requests:
                            break
                        response = await driver.think(
                            invocation_id,
                            "talk",
                            {
                                "name": resident.name,
                                "persona": self.resident_service.read_persona(resident.name),
                                "brain_model": resident.brain_model,
                                "brain_reasoning_effort": resident.brain_reasoning_effort,
                            },
                            {
                                "history": self.sessions.public_history(session_id, limit=20),
                                "world_memories": world_memories,
                                "current_residents": list(self.resident_service.enabled_names),
                                "skills": self.skill_registry.prompt_context(),
                            },
                        )
                    if request_id in self._cancelled_requests:
                        LOGGER.info(
                            "brain_result_discarded_cancelled request_id=%s invocation_id=%s resident=%s",
                            request_id,
                            invocation_id,
                            resident.name,
                        )
                        break
                    LOGGER.info(
                        "brain_success request_id=%s invocation_id=%s elapsed_ms=%d has_say=%s pass=%s resident=%s",
                        request_id,
                        invocation_id,
                        round((perf_counter() - started_at) * 1000),
                        bool(response.say),
                        response.passed,
                        resident.name,
                    )
                    if response.say:
                        entry = self.sessions.append_resident_say(
                            session_id,
                            resident.name,
                            response.say,
                            request_id,
                        )
                        self.world_memory.record_public_entry(entry)
                        await self._publish_holo_public_entry(entry)
                        await websocket.send(make_message("chat_append", {"entry": entry}))
                        await self._send_session_list(websocket)
                        LOGGER.info(
                            "resident_say_saved request_id=%s invocation_id=%s session_id=%s resident=%s",
                            request_id,
                            invocation_id,
                            session_id,
                            resident.name,
                        )
                except (BrainError, ResidentError) as exc:
                    if request_id in self._cancelled_requests:
                        LOGGER.info(
                            "brain_cancelled request_id=%s invocation_id=%s elapsed_ms=%d resident=%s",
                            request_id,
                            invocation_id,
                            round((perf_counter() - started_at) * 1000),
                            resident.name,
                        )
                        break
                    safe_error = str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n")
                    LOGGER.warning(
                        "brain_failed request_id=%s invocation_id=%s elapsed_ms=%d error_type=%s error=%s resident=%s",
                        request_id,
                        invocation_id,
                        round((perf_counter() - started_at) * 1000),
                        type(exc).__name__,
                        safe_error,
                        resident.name,
                    )
                    await websocket.send(
                        make_message(
                            "notice",
                            {"level": "WARN", "text": str(exc)},
                        )
                    )
                finally:
                    self._invocation_drivers.pop(invocation_id, None)
                    invocation_ids = self._request_invocations.get(request_id)
                    if invocation_ids is not None:
                        invocation_ids.discard(invocation_id)
                        if not invocation_ids:
                            self._request_invocations.pop(request_id, None)
        finally:
            self._cancelled_requests.discard(request_id)
            self._request_invocations.pop(request_id, None)
            try:
                await websocket.send(
                    make_message(
                        "response_state",
                        {
                            "active": False,
                            "request_id": request_id,
                            "session_id": session_id,
                        },
                    )
                )
            except Exception:
                pass


    async def _respond_to_whisper(
        self,
        websocket: ServerConnection,
        request_id: str,
        session_id: str,
        resident_name: str,
    ) -> None:
        await websocket.send(
            make_message(
                "response_state",
                {
                    "active": True,
                    "request_id": request_id,
                    "session_id": session_id,
                },
            )
        )
        try:
            resident = self.resident_service.load(resident_name)
        except ResidentError as exc:
            await websocket.send(make_message("notice", {"level": "WARN", "text": str(exc)}))
            await websocket.send(make_message("response_state", {
                "active": False,
                "request_id": request_id,
                "session_id": session_id,
            }))
            return
        if resident.brain is None:
            LOGGER.info(
                "whisper_brain_skipped_no_provider request_id=%s session_id=%s resident=%s",
                request_id,
                session_id,
                resident_name,
            )
            await websocket.send(make_message("response_state", {
                "active": False,
                "request_id": request_id,
                "session_id": session_id,
            }))
            return

        invocation_id = f"INV-{uuid4()}"
        self._request_invocations.setdefault(request_id, set()).add(invocation_id)
        started_at = perf_counter()
        LOGGER.info(
            "brain_start request_id=%s invocation_id=%s session_id=%s resident=%s provider=%s mode=whisper",
            request_id,
            invocation_id,
            session_id,
            resident.name,
            resident.brain,
        )
        try:
            if request_id in self._cancelled_requests:
                return
            private_context = self.private_memory.context_for_brain(resident.name, session_id)
            current_whisper_history = self.sessions.whisper_history(
                session_id,
                resident.name,
                limit=20,
            )
            memory_query = (
                current_whisper_history[-1].get("text", "")
                if current_whisper_history
                else ""
            )
            current_public_history = self.sessions.public_history(session_id, limit=20)
            world_memories = self._world_memory_context(
                memory_query if isinstance(memory_query, str) else "",
                recent_public_entries=current_public_history,
            )
            driver = self._get_brain_driver(resident.brain)
            self._invocation_drivers[invocation_id] = driver
            async with self._brain_call_lock:
                if request_id in self._cancelled_requests:
                    return
                response = await driver.think(
                    invocation_id,
                    "whisper",
                    {
                        "name": resident.name,
                        "persona": self.resident_service.read_persona(resident.name),
                        "brain_model": resident.brain_model,
                        "brain_reasoning_effort": resident.brain_reasoning_effort,
                    },
                    {
                        **private_context,
                        "world_memories": world_memories,
                        "current_residents": list(self.resident_service.enabled_names),
                        "skills": self.skill_registry.prompt_context(),
                        "public_history": self.sessions.public_history(session_id, limit=20),
                        "current_whisper_history": current_whisper_history,
                    },
                )
            if request_id in self._cancelled_requests:
                LOGGER.info(
                    "brain_result_discarded_cancelled request_id=%s invocation_id=%s",
                    request_id,
                    invocation_id,
                )
                return
            LOGGER.info(
                "brain_success request_id=%s invocation_id=%s elapsed_ms=%d has_say=%s pass=%s mode=whisper",
                request_id,
                invocation_id,
                round((perf_counter() - started_at) * 1000),
                bool(response.say),
                response.passed,
            )
            if response.say:
                entry = self.sessions.append_resident_whisper(
                    session_id,
                    resident.name,
                    response.say,
                    request_id,
                )
                self.private_memory.append_whisper(
                    resident.name,
                    session_id=session_id,
                    sender=resident.name,
                    recipient="master",
                    text=entry["text"],
                    request_id=request_id,
                    ts=entry["ts"],
                )
                await websocket.send(make_message("chat_append", {"entry": entry}))
                await self._send_session_list(websocket)
                LOGGER.info(
                    "resident_whisper_saved request_id=%s invocation_id=%s session_id=%s resident=%s",
                    request_id,
                    invocation_id,
                    session_id,
                    resident.name,
                )
        except (BrainError, ResidentError, PrivateMemoryError) as exc:
            if request_id in self._cancelled_requests:
                LOGGER.info(
                    "brain_cancelled request_id=%s invocation_id=%s elapsed_ms=%d mode=whisper",
                    request_id,
                    invocation_id,
                    round((perf_counter() - started_at) * 1000),
                )
            else:
                safe_error = str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n")
                LOGGER.warning(
                    "brain_failed request_id=%s invocation_id=%s elapsed_ms=%d error_type=%s error=%s mode=whisper",
                    request_id,
                    invocation_id,
                    round((perf_counter() - started_at) * 1000),
                    type(exc).__name__,
                    safe_error,
                )
                await websocket.send(make_message("notice", {"level": "WARN", "text": str(exc)}))
        finally:
            self._invocation_drivers.pop(invocation_id, None)
            invocation_ids = self._request_invocations.get(request_id)
            if invocation_ids is not None:
                invocation_ids.discard(invocation_id)
                if not invocation_ids:
                    self._request_invocations.pop(request_id, None)
            self._cancelled_requests.discard(request_id)
            try:
                await websocket.send(make_message("response_state", {
                    "active": False,
                    "request_id": request_id,
                    "session_id": session_id,
                }))
            except Exception:
                pass

    async def _cancel_all_resident_chats(self) -> None:
        for invocation_id in tuple(self._resident_chat_invocations):
            driver = self._invocation_drivers.get(invocation_id)
            if driver is not None:
                await driver.cancel(invocation_id)

        tasks = [task for task in self._resident_chat_tasks if not task.done()]
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _restore_resident_chat_stand(
        self,
        websocket: ServerConnection,
        participant_names: tuple[str, ...],
    ) -> None:
        cleanup_tasks = [
            asyncio.create_task(
                self._request_world_action(
                    websocket,
                    name,
                    "stand",
                    {},
                    timeout_sec=RESIDENT_CHAT_STAND_CLEANUP_TIMEOUT_SEC,
                )
            )
            for name in participant_names
        ]
        if not cleanup_tasks:
            return
        try:
            await asyncio.wait_for(
                asyncio.gather(*cleanup_tasks, return_exceptions=True),
                timeout=RESIDENT_CHAT_STAND_CLEANUP_TIMEOUT_SEC,
            )
        except TimeoutError:
            LOGGER.info(
                "resident_chat_stand_cleanup_timeout participant_count=%s",
                len(participant_names),
            )
        finally:
            for task in cleanup_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

    async def _request_world_action(
        self,
        websocket: ServerConnection,
        resident_name: str,
        command: str,
        args: dict[str, Any],
        *,
        timeout_sec: float = 60.0,
        tolerate_world_disconnect: bool = False,
    ) -> bool:
        action_id = f"ACT-{uuid4()}"
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._action_waiters[action_id] = waiter
        try:
            await websocket.send(make_message(
                "action",
                {"name": resident_name, "command": command, "args": args},
                action_id,
            ))
            result = await asyncio.wait_for(waiter, timeout=timeout_sec)
            if result.get("world_disconnected") is True and not tolerate_world_disconnect:
                raise asyncio.CancelledError
            return result.get("ok") is True
        except TimeoutError:
            LOGGER.warning(
                "world_action_timeout action_id=%s resident=%s command=%s",
                action_id,
                resident_name,
                command,
            )
            return False
        except asyncio.CancelledError:
            LOGGER.info(
                "world_action_cancelled action_id=%s resident=%s command=%s",
                action_id,
                resident_name,
                command,
            )
            raise
        finally:
            self._action_waiters.pop(action_id, None)

    async def run_group_resident_chat(
        self,
        participant_names: list[str] | tuple[str, ...],
        initiator_name: str,
        initial_text: str,
        *,
        initial_to: str | None = None,
        session_id: str | None = None,
        websocket: ServerConnection | None = None,
    ) -> list[dict[str, Any]]:
        current_task = asyncio.current_task()
        if current_task is not None:
            self._resident_chat_tasks.add(current_task)
        try:
            return await self._run_group_resident_chat_impl(
                participant_names,
                initiator_name,
                initial_text,
                initial_to=initial_to,
                session_id=session_id,
                websocket=websocket,
            )
        finally:
            if current_task is not None:
                self._resident_chat_tasks.discard(current_task)

    async def _run_group_resident_chat_impl(
        self,
        participant_names: list[str] | tuple[str, ...],
        initiator_name: str,
        initial_text: str,
        *,
        initial_to: str | None = None,
        session_id: str | None = None,
        websocket: ServerConnection | None = None,
    ) -> list[dict[str, Any]]:
        """Run one public 2-10 Resident group conversation.

        The state machine is participant-count agnostic. A pass only means the
        Resident has nothing to add right now; any later substantive response
        clears old pass state so previously quiet Residents can rejoin.
        """
        participants = tuple(name.strip() for name in participant_names if isinstance(name, str))
        try:
            state = GroupConversationState(participants, initiator_name)
        except GroupConversationError as exc:
            raise ResidentError(str(exc)) from exc
        if not initial_text.strip():
            raise ResidentError("resident_chat initial text must not be empty")

        residents: dict[str, Any] = {}
        for name in participants:
            if name not in self.resident_service.enabled_names:
                raise ResidentError(f"Resident is not enabled: {name}")
            resident = self.resident_service.load(name)
            if resident.brain is None:
                raise ResidentError("resident_chat participants require Brain providers")
            if resident.brain == HOLO_ADDON_BRAIN:
                raise ResidentError(
                    f"{name}はHolo Addonで会話するため、Resident同士の会話には参加できません"
                )
            residents[name] = resident

        initiator = residents[initiator_name]
        initial_address = state.normalize_address(initiator_name, initial_to)
        if len(participants) == 2 and initial_address is None:
            initial_address = next(name for name in participants if name != initiator_name)

        target_session_id = session_id or self.sessions.active_session_id
        if not self.sessions.store.has_session(target_session_id):
            raise ChatStoreError(f"unknown chat session: {target_session_id}")

        target_websocket = websocket or self._world_connection
        if target_websocket is not None:
            try:
                if len(participants) == 2:
                    target_name = initial_address or next(
                        name for name in participants if name != initiator_name
                    )
                    approached = await self._request_world_action(
                        target_websocket,
                        initiator_name,
                        "approach",
                        {"target": target_name},
                    )
                    if not approached:
                        LOGGER.warning(
                            "resident_chat_approach_failed initiator=%s target=%s",
                            initiator_name,
                            target_name,
                        )
                    await self._request_world_action(
                        target_websocket,
                        initiator_name,
                        "face",
                        {"target": target_name},
                    )
                    await self._request_world_action(
                        target_websocket,
                        target_name,
                        "face",
                        {"target": initiator_name},
                    )
                else:
                    gathered = await self._request_world_action(
                        target_websocket,
                        initiator_name,
                        "gather",
                        {"participants": list(participants)},
                    )
                    if not gathered:
                        LOGGER.warning(
                            "resident_group_chat_gather_failed initiator=%s participant_count=%s",
                            initiator_name,
                            len(participants),
                        )
            except asyncio.CancelledError:
                await self._restore_resident_chat_stand(target_websocket, participants)
                raise

        entries: list[dict[str, Any]] = []
        first_entry = self.sessions.append_resident_chat(
            target_session_id,
            initiator_name,
            initial_address,
            initial_text,
        )
        entries.append(first_entry)
        await self._publish_resident_chat_entry(first_entry, websocket)

        previous_speaker = initiator_name
        addressed_to = initial_address
        next_speaker_name = state.next_speaker(previous_speaker, addressed_to)
        try:
            while next_speaker_name is not None and not state.finished:
                speaker = residents[next_speaker_name]
                invocation_id = f"INV-{uuid4()}"
                started_at = perf_counter()
                LOGGER.info(
                    "resident_chat_brain_start invocation_id=%s session_id=%s resident=%s participant_count=%s turn=%s",
                    invocation_id,
                    target_session_id,
                    speaker.name,
                    len(participants),
                    state.turn_count + 1,
                )
                try:
                    assert speaker.brain is not None
                    driver = self._get_brain_driver(speaker.brain)
                    self._invocation_drivers[invocation_id] = driver
                    self._resident_chat_invocations.add(invocation_id)
                    async with self._brain_call_lock:
                        response = await driver.think(
                            invocation_id,
                            "talk",
                            {
                                "name": speaker.name,
                                "persona": self.resident_service.read_persona(speaker.name),
                                "brain_model": speaker.brain_model,
                                "brain_reasoning_effort": speaker.brain_reasoning_effort,
                            },
                            {
                                "history": self.sessions.public_history(target_session_id, limit=20),
                                "world_memories": self._world_memory_context(
                                    str(self.sessions.public_history(target_session_id, limit=1)[-1].get("text", "")),
                                    recent_public_entries=self.sessions.public_history(
                                        target_session_id,
                                        limit=20,
                                    ),
                                ),
                                "current_residents": list(self.resident_service.enabled_names),
                                "skills": self.skill_registry.prompt_context(),
                                "conversation_kind": "resident_chat",
                                "participants": list(participants),
                                "previous_speaker": previous_speaker,
                                "addressed_to": addressed_to,
                            },
                        )
                    next_name, normalized_to = state.record_response(
                        speaker.name,
                        say=response.say,
                        passed=response.passed,
                        addressed_to=response.addressed_to,
                    )
                    effective_to = normalized_to
                    if len(participants) == 2 and effective_to is None:
                        effective_to = next(
                            name for name in participants if name != speaker.name
                        )
                    LOGGER.info(
                        "resident_chat_brain_success invocation_id=%s session_id=%s resident=%s elapsed_ms=%d has_say=%s pass=%s addressed_to=%s",
                        invocation_id,
                        target_session_id,
                        speaker.name,
                        round((perf_counter() - started_at) * 1000),
                        bool(response.say),
                        response.passed,
                        normalized_to,
                    )
                    if response.say:
                        entry = self.sessions.append_resident_chat(
                            target_session_id,
                            speaker.name,
                            effective_to,
                            response.say,
                        )
                        entries.append(entry)
                        await self._publish_resident_chat_entry(entry, websocket)
                    previous_speaker = speaker.name
                    addressed_to = normalized_to
                    next_speaker_name = next_name
                except (BrainError, ResidentError) as exc:
                    next_speaker_name, _ = state.record_response(
                        speaker.name,
                        say="",
                        passed=True,
                    )
                    previous_speaker = speaker.name
                    addressed_to = None
                    LOGGER.warning(
                        "resident_chat_brain_failed invocation_id=%s session_id=%s resident=%s error_type=%s error=%s",
                        invocation_id,
                        target_session_id,
                        speaker.name,
                        type(exc).__name__,
                        str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n"),
                    )
                finally:
                    self._resident_chat_invocations.discard(invocation_id)
                    self._invocation_drivers.pop(invocation_id, None)
        finally:
            if target_websocket is not None:
                await self._restore_resident_chat_stand(target_websocket, participants)
        return entries

    async def run_resident_chat(
        self,
        initiator_name: str,
        target_name: str,
        initial_text: str,
        *,
        session_id: str | None = None,
        websocket: ServerConnection | None = None,
    ) -> list[dict[str, Any]]:
        """Backward-compatible two-Resident wrapper for M2/M3 talk_to()."""
        return await self.run_group_resident_chat(
            [initiator_name, target_name],
            initiator_name,
            initial_text,
            initial_to=target_name,
            session_id=session_id,
            websocket=websocket,
        )

    async def _publish_resident_chat_entry(
        self,
        entry: dict[str, Any],
        websocket: ServerConnection | None,
    ) -> None:
        self.world_memory.record_public_entry(entry)
        await self._publish_holo_public_entry(entry)
        target_websocket = websocket or self._world_connection
        if target_websocket is None:
            return
        try:
            await target_websocket.send(make_message("chat_append", {"entry": entry}))
            await self._send_session_list(target_websocket)
        except Exception:
            LOGGER.warning(
                "resident_chat_world_publish_failed session_id=%s sender=%s",
                entry.get("session"),
                entry.get("from"),
                exc_info=True,
            )

    async def _send_hello_ack(self, websocket: ServerConnection, message_id: str | None) -> None:
        await websocket.send(
            make_message(
                "hello_ack",
                {
                    "residents": [resident.to_protocol() for resident in self.resident_service.list_enabled()],
                    "locations": [],
                    "time_of_day": time_of_day(),
                    "settings": {"audio_volume": self.audio_volume},
                    "active_session": self.sessions.active_session_id,
                    "holo_addon": self.holo_addon_state(),
                },
                message_id,
            )
        )

    async def _send_session_list(
        self,
        websocket: ServerConnection,
        message_id: str | None = None,
    ) -> None:
        await websocket.send(
            make_message(
                "chat_session_list",
                {
                    "sessions": self.sessions.list_sessions(),
                    "active_session": self.sessions.active_session_id,
                },
                message_id,
            )
        )

    async def _send_history(
        self,
        websocket: ServerConnection,
        session_id: str,
        *,
        before: str | None = None,
        limit: int = 50,
        message_id: str | None = None,
    ) -> None:
        entries, next_before = self.sessions.history_page(
            session_id,
            before=before,
            limit=limit,
        )
        await websocket.send(
            make_message(
                "history_response",
                {
                    "session_id": session_id,
                    "entries": entries,
                    "next_before": next_before,
                },
                message_id,
            )
        )
