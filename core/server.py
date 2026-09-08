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
    AgentResourceBusyError,
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
from .brains.native_conversation import (
    NativeConversationBrainDriver,
    NativeConversationBrainService,
)
from .brains.gemini import (
    GeminiDriver,
    GEMINI_DEFAULT_MODEL,
    is_antigravity_model,
    list_gemini_models,
    load_gemini_api_key,
)
from .config import ConfigError, NiraiConfig, save_audio_volume
from .conversation import (
    CONVERSATION_MODES,
    CONVERSATION_TEXT_LIMIT,
    ConversationRecord,
    ConversationRuntimeError,
    ConversationStore,
    GroupConversationError,
    GroupConversationState,
)
from .incidents import (
    IncidentStore,
    IncidentStoreError,
    memory_outbox_fingerprint,
    memory_outbox_unreadable_fingerprint,
)
from .holo import (
    HOLO_ATTACH_WINDOW_DEFAULT_SEC,
    HoloAuthorization,
    HoloAuthorizationError,
    HoloDiveBinding,
    HoloEventQueue,
    HoloEventWaitResult,
)
from .memory import (
    GeminiPrivateEmbeddingProcessor,
    GeminiWorldMemoryProcessor,
    PrivateMemoryBackgroundWorker,
    PrivateMemoryError,
    PrivateMemoryHybridRetriever,
    PrivateMemoryService,
    PrivateSemanticMemoryError,
    PrivateVectorStore,
    StructuredMemoryProcessorError,
    WorldMemoryBackgroundWorker,
    WorldMemoryError,
    WorldMemoryHybridRetriever,
    WorldMemoryRecallError,
    WorldMemoryRetriever,
    WorldMemoryRetrieverError,
    WorldMemoryService,
    WorldStructuredMemoryStore,
)
from .protocol import (
    CORE_CAPABILITIES,
    CORE_RUNTIME_ID,
    PROTOCOL_VERSION,
    ProtocolError,
    make_message,
    parse_message,
    parse_runtime_descriptor,
    runtime_descriptor,
    time_of_day,
)
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
HOLO_REVIEW_WAIT_MAX_SEC = 15.0
HOLO_REVIEW_TASK_PREFIX = "HR-"
HOLO_CONVERSATION_WAIT_MAX_SEC = 15.0
HOLO_CONVERSATION_TASK_PREFIX = "HC-"
HOLO_CONVERSATION_PROVIDERS = frozenset({"cursor", "codex"})
AGENT_SNAPSHOT_EVENT_LIMIT = 500
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
        try:
            self.incidents: IncidentStore | None = IncidentStore(config.root)
        except IncidentStoreError:
            self.incidents = None
            LOGGER.warning("incident_store_unavailable", exc_info=True)
        self.host = CORE_HOST
        self.port = config.core.port if port_override is None else port_override
        self._server: Any | None = None
        self._world_connection: ServerConnection | None = None
        self._world_runtime: tuple[str, tuple[str, ...]] | None = None
        self._brain_driver_override = brain_driver
        self._brain_drivers: dict[str, BrainDriver] = {}
        self._invocation_drivers: dict[str, BrainDriver] = {}
        self._native_brain = NativeConversationBrainService(config.root)
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
        self._conversation_store = ConversationStore(config.root)
        self._conversation_tasks: dict[str, asyncio.Task[None]] = {}
        self._provider_context_cleanup_tasks: dict[str, asyncio.Task[None]] = {}
        self._conversation_wait_events: dict[str, asyncio.Event] = {}
        self._conversation_agent_sessions: dict[str, str] = {}
        self._conversation_invocations: dict[str, str] = {}
        self._conversation_public_sessions: dict[str, str] = {}
        self.audio_volume = config.world.audio_volume
        self.resident_service = ResidentService(config.root, config.residents_enabled)
        self.private_memory = PrivateMemoryService(config.root)
        self.private_hybrid_retriever = PrivateMemoryHybridRetriever(self.private_memory)
        self._private_embedding_processor: GeminiPrivateEmbeddingProcessor | None = None
        self._private_vector_store: PrivateVectorStore | None = None
        self._private_memory_worker: PrivateMemoryBackgroundWorker | None = None
        self._private_memory_worker_task: asyncio.Task[None] | None = None
        self.world_memory = WorldMemoryService(config.root)
        self.world_retriever = WorldMemoryRetriever(config.root)
        self._world_memory_processor: GeminiWorldMemoryProcessor | None = None
        self._world_structured_store: WorldStructuredMemoryStore | None = None
        self.world_hybrid_retriever = WorldMemoryHybridRetriever(
            config.root,
            query_embedding_daily_limit=config.memory.query_embedding_daily_limit,
            embedding_daily_budget=config.memory.embedding_daily_budget,
        )
        self._world_memory_worker: WorldMemoryBackgroundWorker | None = None
        self._world_memory_worker_task: asyncio.Task[None] | None = None
        self._initialize_world_memory_worker()
        self._initialize_private_memory_worker()
        self.sessions = SessionManager(ChatStore(config.root / "runtime" / "chat_sessions"))
        # Chat JSONL is authoritative and may be one fsync ahead of the
        # rebuildable SQLite index/outbox after a hard crash. Reconcile every
        # durable tail before attempting cross-store Memory replay.
        self.sessions.store.reconcile_raw_sessions()
        self._reconcile_memory_outbox()
        self.agent_runtime = AgentRuntimeManager(
            config.root,
            config.tasks_allowed_dirs,
            broadcast=self._broadcast_agent_event,
        )
        self.skill_registry = SkillRegistry(config.root / "skills")
        self.agent_runtime.set_work_prompt_enricher(self.skill_registry.augment_task_prompt)
        self._task_queue_store_error: str | None = None
        self._restore_agent_task_state()
        self._restore_task_queue_state()
        self._restore_holo_binding_state()

    def _record_public_memory_entry(self, entry: dict[str, Any]) -> None:
        self.world_memory.record_public_entry(entry)
        entry_id = entry.get("entry_id")
        if isinstance(entry_id, str) and entry_id:
            self.sessions.store.mark_memory_synced(entry_id)

    def _record_private_memory_entry(self, resident_name: str, entry: dict[str, Any]) -> None:
        self.private_memory.append_whisper(
            resident_name,
            session_id=str(entry.get("session", "")),
            sender=str(entry.get("from", "")),
            recipient=str(entry.get("to", "")),
            text=str(entry.get("text", "")),
            request_id=(entry.get("request_id") if isinstance(entry.get("request_id"), str) else None),
            ts=(entry.get("ts") if isinstance(entry.get("ts"), str) else None),
            entry_id=(entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None),
        )
        entry_id = entry.get("entry_id")
        if isinstance(entry_id, str) and entry_id:
            self.sessions.store.mark_memory_synced(entry_id)

    def _record_incident(
        self,
        *,
        component: str,
        code: str,
        severity: str,
        summary: str,
        detail: str = "",
        error_type: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        store = self.incidents
        if store is None:
            return
        try:
            store.record(
                component=component,
                code=code,
                severity=severity,
                summary=summary,
                detail=detail,
                error_type=error_type,
                fingerprint=fingerprint,
            )
        except IncidentStoreError:
            LOGGER.warning("incident_record_failed code=%s", code, exc_info=True)

    def _reconcile_memory_outbox(
        self,
        *,
        max_batches: int | None = None,
        batch_size: int = 500,
    ) -> None:
        """Replay Chat-authoritative memory writes after partial cross-store failure.

        Startup uses the default unbounded convergence. Interactive health checks
        pass a small batch cap so diagnostics can never turn a Dive into a long
        repair stall.
        """
        repaired = 0
        batches = 0
        replay_failure_count = 0
        replay_failure_sample: tuple[str, str] | None = None
        outbox_read_error: ChatStoreError | None = None
        bounded_batch_size = min(max(int(batch_size), 1), 500)
        while True:
            if max_batches is not None and batches >= max(0, int(max_batches)):
                break
            try:
                pending = self.sessions.store.pending_memory_sync(limit=bounded_batch_size)
            except ChatStoreError as exc:
                outbox_read_error = exc
                break
            batches += 1
            if not pending:
                break
            progressed = False
            for item in pending:
                entry_id = str(item["entry_id"])
                try:
                    entry = item["entry"]
                    if item["scope"] == "world":
                        self._record_public_memory_entry(entry)
                    elif item["scope"] == "private" and isinstance(item.get("resident_name"), str):
                        self.private_memory.append_whisper(
                            str(item["resident_name"]),
                            session_id=str(entry.get("session", "")),
                            sender=str(entry.get("from", "")),
                            recipient=str(entry.get("to", "")),
                            text=str(entry.get("text", "")),
                            request_id=(entry.get("request_id") if isinstance(entry.get("request_id"), str) else None),
                            ts=(entry.get("ts") if isinstance(entry.get("ts"), str) else None),
                            entry_id=(entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None),
                        )
                    else:
                        LOGGER.error("memory_outbox_invalid entry_id=%s scope=%s", entry_id, item.get("scope"))
                        continue
                    self.sessions.store.mark_memory_synced(entry_id)
                    repaired += 1
                    progressed = True
                except (WorldMemoryError, PrivateMemoryError, ChatStoreError, OSError) as exc:
                    replay_failure_count += 1
                    if replay_failure_sample is None:
                        replay_failure_sample = (
                            entry_id,
                            f"{type(exc).__name__}: {exc}",
                        )
            if not progressed or len(pending) < bounded_batch_size:
                break
        if repaired:
            LOGGER.info("memory_outbox_reconciled count=%s", repaired)
        try:
            pending_count = self.sessions.store.pending_memory_sync_count()
        except ChatStoreError as exc:
            pending_count = -1
            if outbox_read_error is None:
                outbox_read_error = exc
        if outbox_read_error is not None:
            LOGGER.warning(
                "memory_outbox_unreadable error=%s",
                str(outbox_read_error)[:500].replace("\r", "\\r").replace("\n", "\\n"),
            )
            self._record_incident(
                component="nirai.core.memory",
                code="memory_outbox_unreadable",
                severity="error",
                summary="Memory Outboxを読み取れず自動再同期できない",
                detail=f"{type(outbox_read_error).__name__}: {outbox_read_error}",
                error_type=type(outbox_read_error).__name__,
                fingerprint=memory_outbox_unreadable_fingerprint(),
            )
        elif batches > 0 and self.incidents is not None:
            try:
                self.incidents.resolve_fingerprint(
                    memory_outbox_unreadable_fingerprint(),
                    "Memory outbox became readable again",
                )
            except IncidentStoreError:
                LOGGER.warning("incident_auto_resolve_failed code=memory_outbox_unreadable", exc_info=True)
        if replay_failure_count and pending_count != 0:
            sample_entry_id, sample_error = replay_failure_sample or ("unknown", "unknown")
            LOGGER.warning(
                "memory_outbox_replay_failed count=%s sample_entry_id=%s error=%s",
                replay_failure_count,
                sample_entry_id,
                sample_error[:500].replace("\r", "\\r").replace("\n", "\\n"),
            )
            self._record_incident(
                component="nirai.core.memory",
                code="memory_outbox_pending",
                severity="error",
                summary=(
                    "Chatには保存済みだがMemoryへの再同期が完了していないentryがある "
                    f"(failed={replay_failure_count}, pending={pending_count})"
                ),
                detail=f"sample_entry_id={sample_entry_id}; error={sample_error}",
                error_type=sample_error.split(":", 1)[0] if ":" in sample_error else None,
                fingerprint=memory_outbox_fingerprint(),
            )
        if pending_count == 0 and self.incidents is not None:
            try:
                self.incidents.resolve_fingerprint(
                    memory_outbox_fingerprint(),
                    "Memory outbox replay completed automatically",
                )
            except IncidentStoreError:
                LOGGER.warning("incident_auto_resolve_failed code=memory_outbox_pending", exc_info=True)

    def _initialize_private_memory_worker(self) -> None:
        settings = self.config.memory
        if settings.private_semantic_provider != "gemini":
            return
        try:
            processor = GeminiPrivateEmbeddingProcessor(
                self.config.root,
                embedding_model=settings.embedding_model,
                vector_dim=settings.embedding_dim,
            )
            store = PrivateVectorStore(
                self.private_memory,
                vector_dim=settings.embedding_dim,
                embedding_model=settings.embedding_model,
            )
            # Public and Private use the same Gemini Embedding 2 quota budget.
            # If Public derived memory is disabled, create only the local quota
            # authority; this does not enable Public semantic recall by itself.
            quota_store = self._world_structured_store or WorldStructuredMemoryStore(
                self.config.root,
                vector_dim=settings.embedding_dim,
                embedding_model=settings.embedding_model,
            )
            self._private_embedding_processor = processor
            self._private_vector_store = store
            self.private_hybrid_retriever = PrivateMemoryHybridRetriever(
                self.private_memory,
                store=store,
                processor=processor,
                quota_store=quota_store,
                query_embedding_daily_limit=settings.query_embedding_daily_limit,
                embedding_daily_budget=settings.embedding_daily_budget,
            )
            self._private_memory_worker = PrivateMemoryBackgroundWorker(
                store,
                processor,
                quota_store=quota_store,
                daily_limit=settings.background_daily_limit,
                embedding_daily_budget=settings.embedding_daily_budget,
            )
        except (
            PrivateSemanticMemoryError,
            StructuredMemoryProcessorError,
            OSError,
            RuntimeError,
        ):
            LOGGER.warning("private_memory_semantic_unavailable", exc_info=True)
            self._private_embedding_processor = None
            self._private_vector_store = None
            self._private_memory_worker = None
            self.private_hybrid_retriever = PrivateMemoryHybridRetriever(self.private_memory)

    async def _private_memory_worker_loop(self) -> None:
        worker = self._private_memory_worker
        if worker is None:
            return
        interval = self.config.memory.private_background_interval_sec
        while True:
            try:
                resident_names = [
                    name
                    for name in self.resident_service.enabled_names
                    if self.resident_service.load(name).brain != HOLO_ADDON_BRAIN
                ]
                summary = await worker.process_pending(resident_names, limit_total=32)
                if summary.processed or summary.failed or summary.budget_exhausted:
                    LOGGER.info(
                        "private_memory_background processed=%s failed=%s budget_exhausted=%s",
                        summary.processed,
                        summary.failed,
                        summary.budget_exhausted,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning("private_memory_background_failed", exc_info=True)
            await asyncio.sleep(interval)

    def _initialize_world_memory_worker(self) -> None:
        settings = self.config.memory
        if settings.world_processor != "gemini":
            return
        try:
            processor = GeminiWorldMemoryProcessor(
                self.config.root,
                extraction_model=settings.extraction_model,
                embedding_model=settings.embedding_model,
                embedding_dim=settings.embedding_dim,
            )
            store = WorldStructuredMemoryStore(
                self.config.root,
                vector_dim=settings.embedding_dim,
                embedding_model=settings.embedding_model,
            )
            self._world_memory_processor = processor
            self._world_structured_store = store
            self.world_hybrid_retriever = WorldMemoryHybridRetriever(
                self.config.root,
                store=store,
                processor=processor,
                query_embedding_daily_limit=settings.query_embedding_daily_limit,
                embedding_daily_budget=settings.embedding_daily_budget,
            )
            self._world_memory_worker = WorldMemoryBackgroundWorker(
                store,
                processor,
                daily_limit=settings.background_daily_limit,
                embedding_daily_budget=settings.embedding_daily_budget,
            )
        except (StructuredMemoryProcessorError, OSError, RuntimeError):
            # Cloud-derived memory is optional. Raw World Memory and local FTS
            # remain available even when Gemini credentials, sqlite-vec, or the
            # derived schema are temporarily unavailable.
            LOGGER.warning("world_memory_processor_unavailable", exc_info=True)
            self._world_memory_processor = None
            self._world_structured_store = None
            self._world_memory_worker = None

    async def _world_memory_worker_loop(self) -> None:
        worker = self._world_memory_worker
        if worker is None:
            return
        interval = self.config.memory.background_interval_sec
        while True:
            try:
                summary = await worker.process_pending(limit=1)
                if summary.processed or summary.failed or summary.vectors_rebuilt or summary.budget_exhausted:
                    LOGGER.info(
                        "world_memory_background processed=%s failed=%s candidates=%s vectors_rebuilt=%s budget_exhausted=%s",
                        summary.processed,
                        summary.failed,
                        summary.candidates_committed,
                        summary.vectors_rebuilt,
                        summary.budget_exhausted,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never make the conversation server depend on derived-memory
                # background health. Raw entries remain queued for later retry.
                LOGGER.warning("world_memory_background_iteration_failed", exc_info=True)
            await asyncio.sleep(interval)

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

    def holo_incidents_authorized(self, *, limit: int = 20) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        store = self.incidents
        if store is None:
            return {"available": False, "incidents": []}
        try:
            return {
                "available": True,
                "incidents": store.unresolved(limit=min(max(int(limit), 1), 50)),
            }
        except IncidentStoreError:
            LOGGER.warning("holo_incident_read_failed", exc_info=True)
            return {"available": False, "incidents": []}

    def holo_resolve_incident_authorized(self, incident_id: str, note: str = "") -> bool:
        self._holo_authorization.require_attached()
        store = self.incidents
        if store is None:
            raise IncidentStoreError("Incident store is unavailable")
        return store.resolve(incident_id, note)

    def holo_skills_authorized(self) -> dict[str, object]:
        self._holo_authorization.require_attached()
        return self.skill_registry.public_payload()

    async def holo_wait_events_authorized(
        self,
        after_event_id: int,
        *,
        timeout_sec: float,
        limit: int = 50,
        event_epoch: str | None = None,
    ) -> HoloEventWaitResult:
        self._holo_authorization.require_attached()
        return await self.holo_wait_events(
            after_event_id,
            timeout_sec=timeout_sec,
            limit=limit,
            event_epoch=event_epoch,
        )

    async def holo_world_say_authorized(
        self,
        text: str,
        *,
        to: str | None = None,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        return await self.holo_world_say(text, to=to)

    def _conversation_wait_event(self, conversation_id: str) -> asyncio.Event:
        event = self._conversation_wait_events.get(conversation_id)
        if event is None:
            event = asyncio.Event()
            self._conversation_wait_events[conversation_id] = event
        return event

    def _signal_conversation_changed(self, conversation_id: str) -> None:
        self._conversation_wait_event(conversation_id).set()

    def _conversation_record(self, conversation_id: str) -> ConversationRecord:
        return self._conversation_store.load(conversation_id)

    def holo_conversation_start_authorized(
        self,
        participant_kind: str,
        participant: str,
        mode: str,
        *,
        target_name: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        cleaned_kind = participant_kind.strip().casefold()
        cleaned_participant = participant.strip()
        cleaned_mode = mode.strip().casefold()
        if cleaned_mode not in CONVERSATION_MODES:
            raise ConversationRuntimeError(
                "Conversation mode must be talk, brainstorm, consult, or review"
            )
        if cleaned_kind == "resident":
            resident = self.resident_service.load(cleaned_participant)
            if resident.brain is None:
                raise ConversationRuntimeError(
                    f"Resident has no Brain provider: {resident.name}"
                )
            if resident.brain == HOLO_ADDON_BRAIN:
                raise ConversationRuntimeError("Holo cannot open a Conversation with itself")
            if cleaned_mode == "review":
                raise ConversationRuntimeError("Resident Conversation does not support review mode")
            if target_name is not None and target_name.strip():
                raise ConversationRuntimeError(
                    "Resident Conversation cannot receive a project working directory"
                )
            cleaned_participant = resident.name
            target_name = None
        elif cleaned_kind == "provider":
            provider = cleaned_participant.casefold()
            if provider not in HOLO_CONVERSATION_PROVIDERS:
                raise ConversationRuntimeError(
                    "Holo Conversation currently supports Cursor and Codex providers"
                )
            if not self.agent_runtime.supports_provider(provider):
                raise ConversationRuntimeError(
                    f"Agent Runtime provider is unavailable: {provider}"
                )
            cleaned_participant = provider
            if cleaned_mode == "review" and (target_name is None or not target_name.strip()):
                raise ConversationRuntimeError("Review Conversation requires a target folder name")
            if target_name is not None and target_name.strip():
                # Validate the named read-only source at Conversation creation,
                # then validate again immediately before every provider turn.
                self.agent_runtime.workspace_policy.named_review_working_dir(
                    target_name,
                    task_id=f"{HOLO_CONVERSATION_TASK_PREFIX}{uuid4()}",
                )
        else:
            raise ConversationRuntimeError(
                "Conversation participant_kind must be resident or provider"
            )

        record = self._conversation_store.create(
            participant_kind=cleaned_kind,
            participant=cleaned_participant,
            mode=cleaned_mode,
            target_name=target_name,
            model=model,
            reasoning_effort=reasoning_effort,
        )
        self._conversation_wait_event(record.conversation_id).set()
        return record.to_protocol()

    @staticmethod
    def _conversation_history_lines(
        record: ConversationRecord,
        *,
        limit: int = 20,
        messages: tuple[Any, ...] | None = None,
    ) -> list[str]:
        lines: list[str] = []
        selected = record.messages[-limit:] if messages is None else messages[-limit:]
        for message in selected:
            label = "Holo" if message.role == "holo" else message.sender
            lines.append(f"{label}: {message.text}")
        return lines

    def _provider_conversation_prompt(self, record: ConversationRecord) -> str:
        latest = record.messages[-1].text if record.messages else ""
        recovery_context = ""
        if record.provider_session_id is None:
            full_messages = self._conversation_store.full_messages(record)
            prior_messages = full_messages[:-1]
            if any(message.role == "participant" for message in prior_messages):
                # Recovery path only. Normal Provider conversations keep their
                # native Cursor session / Codex thread id, so prior turns are
                # not re-sent every request. The append-only Nirai journal is
                # authoritative when the provider-native cache is lost.
                recovery_context = (
                    "\n\nNirai recovery transcript (used only because native Provider context is unavailable):\n"
                    + "\n".join(
                        self._conversation_history_lines(
                            record,
                            limit=len(prior_messages),
                            messages=tuple(prior_messages),
                        )
                    )
                )
        mode_instruction = {
            "talk": (
                "This is a direct conversation with Holo. Respond naturally to the latest Holo message."
            ),
            "brainstorm": (
                "This is a brainstorming session with Holo. Explore useful options, trade-offs, and alternatives. "
                "Do not make changes."
            ),
            "consult": (
                "This is a technical/specification consultation with Holo. Use the read-only project context when "
                "relevant and answer the latest point directly. Do not make changes."
            ),
            "review": (
                "This is an independent code review requested by Holo. Inspect the read-only project context. "
                "The first non-empty line of the final answer must be exactly SAFE or NEEDS FIX. If NEEDS FIX, "
                "give concrete findings with priority, file, line or symbol, and reason. Do not fix anything."
            ),
        }[record.mode]
        return f"""Nirai Conversation {record.conversation_id}
Counterparty: Holo
Mode: {record.mode}

{mode_instruction}
Native Provider conversation state is the primary continuity mechanism. The latest Holo message below is new input for this turn.
Do not require Nirai to repeat earlier turns when the native session/thread is available.

Latest Holo message:
{latest}{recovery_context}
"""

    def _resident_conversation_history(
        self,
        record: ConversationRecord,
        *,
        messages: tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        history: list[dict[str, Any]] = []
        holo_name = self._holo_resident_name()
        selected = record.messages[-20:] if messages is None else messages
        for message in selected:
            sender = holo_name if message.role == "holo" else record.participant
            recipient = record.participant if message.role == "holo" else holo_name
            history.append({
                "entry_id": f"cvseq:{message.seq}",
                "from": sender,
                "to": recipient,
                "text": message.text,
                "ts": message.ts,
            })
        return history

    def _native_holo_resident_history(
        self,
        record: ConversationRecord,
        provider: str,
    ) -> tuple[list[dict[str, Any]], str, str | None, bool]:
        logical_id = self._holo_resident_brain_conversation_id(
            record.conversation_id,
            record.participant,
        )
        bootstrap = not self._native_brain.has_compatible_state(logical_id, provider)
        last_seen = self._native_brain.last_seen_entry_id(logical_id, provider)
        last_output = self._native_brain.last_output_entry_id(logical_id, provider)
        selected = record.messages
        if not bootstrap and isinstance(last_seen, str) and last_seen.startswith("cvseq:"):
            try:
                after_seq = int(last_seen.split(":", 1)[1])
            except ValueError:
                self._native_brain.reset(logical_id)
                bootstrap = True
            else:
                if record.messages and after_seq < record.messages[0].seq - 1:
                    # The marker fell out of the hot tail. Recover the delta from
                    # Nirai's append-only journal rather than treating the
                    # provider-native cache as irreplaceable state.
                    full_messages = self._conversation_store.full_messages(record)
                    selected = tuple(message for message in full_messages if message.seq > after_seq)
                else:
                    selected = tuple(message for message in record.messages if message.seq > after_seq)
        if bootstrap:
            selected = self._conversation_store.full_messages(record)
        elif last_output is not None and last_output.startswith("cvseq:"):
            try:
                output_seq = int(last_output.split(":", 1)[1])
            except ValueError:
                self._native_brain.reset(logical_id)
                bootstrap = True
                selected = self._conversation_store.full_messages(record)
            else:
                selected = tuple(message for message in selected if message.seq != output_seq)
        if len(selected) > 100 or sum(len(message.text) for message in selected) > 64_000:
            self._native_brain.reset(logical_id)
            bootstrap = True
            selected = record.messages[-20:]
        history = self._resident_conversation_history(record, messages=tuple(selected))
        marker = f"cvseq:{selected[-1].seq}" if selected else last_seen
        return history, logical_id, marker if isinstance(marker, str) else None, bootstrap

    async def _discard_provider_conversation_context_async(
        self,
        provider: str,
        conversation_id: str,
    ) -> None:
        try:
            await asyncio.to_thread(
                self.agent_runtime.discard_conversation_context,
                provider,
                conversation_id,
            )
        except Exception:
            LOGGER.warning(
                "conversation_provider_context_cleanup_failed conversation_id=%s provider=%s",
                conversation_id,
                provider,
                exc_info=True,
            )

    async def _await_provider_conversation_context_cleanup(self, conversation_id: str) -> None:
        task = self._provider_context_cleanup_tasks.get(conversation_id)
        if task is not None:
            await task

    def _schedule_provider_conversation_context_discard(
        self,
        provider: str,
        conversation_id: str,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Non-async maintenance/tests may still call this helper. There is no
            # event loop to protect in that case, so perform the bounded cleanup
            # synchronously and keep the same best-effort cache semantics.
            try:
                self.agent_runtime.discard_conversation_context(provider, conversation_id)
            except Exception:
                LOGGER.warning(
                    "conversation_provider_context_cleanup_failed conversation_id=%s provider=%s",
                    conversation_id,
                    provider,
                    exc_info=True,
                )
            return
        previous = self._provider_context_cleanup_tasks.get(conversation_id)
        if previous is not None and not previous.done():
            return
        task = loop.create_task(
            self._discard_provider_conversation_context_async(provider, conversation_id),
            name=f"provider-context-cleanup-{conversation_id}",
        )
        self._provider_context_cleanup_tasks[conversation_id] = task

        def cleanup_done(finished: asyncio.Task[None]) -> None:
            if self._provider_context_cleanup_tasks.get(conversation_id) is finished:
                self._provider_context_cleanup_tasks.pop(conversation_id, None)

        task.add_done_callback(cleanup_done)

    def _invalidate_provider_conversation_context(
        self,
        record: ConversationRecord,
    ) -> ConversationRecord:
        latest = self._conversation_store.clear_provider_session(record)
        self._schedule_provider_conversation_context_discard(
            latest.participant,
            latest.conversation_id,
        )
        return latest

    async def _publish_resident_conversation_reply(
        self,
        resident_name: str,
        text: str,
        *,
        session_id: str,
    ) -> None:
        try:
            entry = self.sessions.append_resident_chat(
                session_id,
                resident_name,
                self._holo_resident_name(),
                text,
            )
            self._record_public_memory_entry(entry)
            await self._publish_holo_public_entry(entry)
            websocket = self._world_connection
            if websocket is not None:
                await websocket.send(make_message("chat_append", {"entry": entry}))
                await self._send_session_list(websocket)
        except (ChatStoreError, WorldMemoryError, OSError):
            # Conversation Runtime is the durable source of truth. World chat
            # publication is supplemental and must not erase a completed turn.
            LOGGER.warning(
                "conversation_world_reply_publish_failed resident=%s",
                resident_name,
                exc_info=True,
            )
        except Exception:
            LOGGER.warning(
                "conversation_world_reply_transport_failed resident=%s",
                resident_name,
                exc_info=True,
            )

    async def holo_conversation_send_authorized(
        self,
        conversation_id: str,
        text: str,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        cleaned = text.strip()
        if not cleaned:
            raise ConversationRuntimeError("Conversation message must not be empty")
        if len(cleaned) > CONVERSATION_TEXT_LIMIT:
            raise ConversationRuntimeError(
                f"Conversation message exceeds the {CONVERSATION_TEXT_LIMIT} character limit"
            )
        record = self._conversation_record(conversation_id)
        if record.participant_kind == "provider":
            await self._await_provider_conversation_context_cleanup(conversation_id)
            record = self._conversation_record(conversation_id)
        if record.state != "open":
            raise ConversationRuntimeError("Conversation is closed")
        active_task = self._conversation_tasks.get(conversation_id)
        if record.turn_state == "running" or (active_task is not None and not active_task.done()):
            raise ConversationRuntimeError("Conversation already has a running or finalizing turn")

        public_session_id: str | None = None
        if record.participant_kind == "resident" and record.mode == "talk":
            # Validate the supplemental public Chat target before durably marking
            # the Conversation turn running. A missing Chat Session must never
            # leave a turn that has no execution task and can only be cleared by
            # restarting Core.
            public_session_id = self.sessions.active_session_id
            if not self.sessions.store.has_session(public_session_id):
                raise ConversationRuntimeError("Conversation public Chat Session is unavailable")

        record = self._conversation_store.start_turn(
            record,
            holo_sender=self._holo_resident_name(),
            text=cleaned,
        )
        wait_event = self._conversation_wait_event(conversation_id)
        wait_event.clear()

        if record.participant_kind == "resident":
            if record.mode == "talk":
                assert public_session_id is not None
                self._conversation_public_sessions[conversation_id] = public_session_id
                try:
                    await self.holo_world_say(
                        cleaned,
                        to=record.participant,
                        session_id=public_session_id,
                    )
                except (ChatStoreError, ResidentError, WorldMemoryError, OSError):
                    LOGGER.warning(
                        "conversation_world_say_publish_failed conversation_id=%s resident=%s",
                        conversation_id,
                        record.participant,
                        exc_info=True,
                    )
            task = asyncio.create_task(
                self._run_resident_conversation_turn(conversation_id),
                name=f"conversation-resident-{conversation_id}",
            )
            self._conversation_tasks[conversation_id] = task
            task.add_done_callback(
                lambda finished, current_id=conversation_id: self._conversation_task_done(
                    current_id,
                    finished,
                )
            )
            return self._conversation_record(conversation_id).to_protocol()

        try:
            record = await self._start_provider_conversation_turn(record)
        except Exception as exc:
            latest = self._conversation_record(conversation_id)
            if latest.turn_state == "running":
                failed = self._conversation_store.end_turn(
                    latest,
                    "failed",
                    error=str(exc) or type(exc).__name__,
                )
                self._invalidate_provider_conversation_context(failed)
            self._signal_conversation_changed(conversation_id)
            raise
        return record.to_protocol()

    async def _start_provider_conversation_turn(
        self,
        record: ConversationRecord,
    ) -> ConversationRecord:
        if record.participant_kind != "provider":
            raise ConversationRuntimeError("Conversation is not a Provider conversation")
        task_id = f"{HOLO_CONVERSATION_TASK_PREFIX}{uuid4()}"
        working_dir: str | None = None
        if record.target_name is not None:
            resolved = self.agent_runtime.workspace_policy.named_review_working_dir(
                record.target_name,
                task_id=task_id,
            )
            working_dir = str(resolved)
        metadata_dir = self.agent_runtime.workspace_policy.task_metadata_dir(task_id)
        snapshot = await self.agent_runtime.start_session(
            task_id=task_id,
            resident=self._holo_resident_name(),
            provider=record.participant,
            prompt=self._provider_conversation_prompt(record),
            working_dir=working_dir,
            task_metadata_dir=str(metadata_dir),
            model=record.model,
            reasoning_effort=record.reasoning_effort,
            origin_chat_session_id=None,
            read_only=True,
            purpose=record.mode,
            conversation_id=record.conversation_id,
            provider_session_id=record.provider_session_id,
        )
        latest = self._conversation_record(record.conversation_id)
        if latest.turn_state != "running" or latest.active_turn_id != record.active_turn_id:
            await self.agent_runtime.cancel(snapshot.agent_session_id)
            raise ConversationRuntimeError("Conversation turn changed before Provider start completed")
        latest = self._conversation_store.set_active_agent_session(
            latest,
            snapshot.agent_session_id,
        )
        self._conversation_agent_sessions[record.conversation_id] = snapshot.agent_session_id
        task = asyncio.create_task(
            self._monitor_provider_conversation_turn(
                record.conversation_id,
                snapshot.agent_session_id,
            ),
            name=f"conversation-provider-{record.conversation_id}",
        )
        self._conversation_tasks[record.conversation_id] = task
        task.add_done_callback(
            lambda finished, current_id=record.conversation_id: self._conversation_task_done(
                current_id,
                finished,
            )
        )
        return latest

    async def _monitor_provider_conversation_turn(
        self,
        conversation_id: str,
        agent_session_id: str,
    ) -> None:
        try:
            while True:
                payload = self.agent_runtime.snapshot_payload(agent_session_id)
                session = payload["session"]
                state = session.get("run_state")
                if state in TERMINAL_RUN_STATES:
                    break
                await asyncio.sleep(0.05)

            record = self._conversation_record(conversation_id)
            if record.turn_state != "running":
                return
            if record.active_agent_session_id != agent_session_id:
                raise ConversationRuntimeError(
                    "Provider Conversation Agent Session no longer matches the active turn"
                )
            final_summary = session.get("final_summary")
            provider_session_id = session.get("provider_session_id")
            if state == "completed":
                if not isinstance(provider_session_id, str) or not provider_session_id.strip():
                    failed = self._conversation_store.end_turn(
                        record,
                        "failed",
                        error="Provider completed without a resumable native conversation id",
                        agent_session_id=agent_session_id,
                    )
                    self._invalidate_provider_conversation_context(failed)
                elif not isinstance(final_summary, str) or not final_summary.strip():
                    failed = self._conversation_store.end_turn(
                        record,
                        "failed",
                        error="Provider completed without a final Conversation response",
                        agent_session_id=agent_session_id,
                    )
                    self._invalidate_provider_conversation_context(failed)
                else:
                    verdict = (
                        self._holo_review_verdict(final_summary)
                        if record.mode == "review"
                        else None
                    )
                    self._conversation_store.finish_turn(
                        record,
                        sender=record.participant,
                        text=final_summary,
                        agent_session_id=agent_session_id,
                        provider_session_id=provider_session_id,
                        verdict=verdict,
                    )
            elif state == "cancelled":
                cancelled = self._conversation_store.end_turn(
                    record,
                    "cancelled",
                    error="Provider Conversation turn was cancelled",
                    agent_session_id=agent_session_id,
                )
                self._invalidate_provider_conversation_context(cancelled)
            elif state == "interrupted":
                interrupted = self._conversation_store.end_turn(
                    record,
                    "interrupted",
                    error="Provider Conversation turn was interrupted",
                    agent_session_id=agent_session_id,
                )
                self._invalidate_provider_conversation_context(interrupted)
            else:
                latest_error = next(
                    (
                        event.get("payload", {}).get("message")
                        for event in reversed(payload.get("events", []))
                        if event.get("type") == "error"
                        and isinstance(event.get("payload"), dict)
                        and isinstance(event.get("payload", {}).get("message"), str)
                    ),
                    None,
                )
                failed = self._conversation_store.end_turn(
                    record,
                    "failed",
                    error=latest_error or "Provider Conversation turn failed",
                    agent_session_id=agent_session_id,
                )
                self._invalidate_provider_conversation_context(failed)
        except asyncio.CancelledError:
            try:
                await self.agent_runtime.cancel(agent_session_id)
            except (AgentRuntimeManagerError, AgentSessionStoreError):
                pass
            try:
                record = self._conversation_record(conversation_id)
                if record.turn_state == "running":
                    cancelled = self._conversation_store.end_turn(
                        record,
                        "cancelled",
                        error="Conversation turn cancelled",
                        agent_session_id=agent_session_id,
                    )
                    self._invalidate_provider_conversation_context(cancelled)
            except ConversationRuntimeError:
                pass
            raise
        except (
            AgentRuntimeManagerError,
            AgentSessionStoreError,
            ConversationRuntimeError,
        ) as exc:
            try:
                record = self._conversation_record(conversation_id)
                if record.turn_state == "running":
                    failed = self._conversation_store.end_turn(
                        record,
                        "failed",
                        error=str(exc) or type(exc).__name__,
                        agent_session_id=agent_session_id,
                    )
                    self._invalidate_provider_conversation_context(failed)
            except ConversationRuntimeError:
                pass
        finally:
            self._conversation_agent_sessions.pop(conversation_id, None)
            self._signal_conversation_changed(conversation_id)

    async def _run_resident_conversation_turn(self, conversation_id: str) -> None:
        invocation_id = f"CINV-{uuid4()}"
        driver: BrainDriver | None = None
        native_turn_lock: asyncio.Lock | None = None
        native_turn_lock_acquired = False
        try:
            record = self._conversation_record(conversation_id)
            if record.participant_kind != "resident" or record.turn_state != "running":
                raise ConversationRuntimeError("Resident Conversation turn is not runnable")
            resident = self.resident_service.load(record.participant)
            if resident.brain is None or resident.brain == HOLO_ADDON_BRAIN:
                raise ConversationRuntimeError(
                    f"Resident Brain is unavailable for Conversation: {resident.name}"
                )
            record = self._conversation_store.set_active_invocation(record, invocation_id)
            self._conversation_invocations[conversation_id] = invocation_id
            driver = self._get_brain_driver(resident.brain)
            self._invocation_drivers[invocation_id] = driver
            history = self._resident_conversation_history(record)
            native_logical_id: str | None = None
            native_input_marker: str | None = None
            native_bootstrap = False
            if self._native_brain_enabled(resident.brain):
                native_turn_lock = self._native_brain.conversation_lock(
                    self._holo_resident_brain_conversation_id(conversation_id, resident.name)
                )
                await native_turn_lock.acquire()
                native_turn_lock_acquired = True
                (
                    history,
                    native_logical_id,
                    native_input_marker,
                    native_bootstrap,
                ) = self._native_holo_resident_history(record, resident.brain)
            latest_text = record.messages[-1].text if record.messages else ""
            public_session_id = self._conversation_public_sessions.get(conversation_id)
            history_session_id = public_session_id or self.sessions.active_session_id
            public_history = self.sessions.public_history(history_session_id, limit=20)
            world_memories = await self._world_memory_context(
                latest_text,
                recent_public_entries=public_history,
                session_id=history_session_id,
            )
            brain_context: dict[str, Any] = {
                "history": history,
                "world_memories": world_memories,
                "current_residents": list(self.resident_service.enabled_names),
                "conversation_kind": "resident_chat",
                "counterpart": self._holo_resident_name(),
            }
            if native_logical_id is not None:
                brain_context.update({
                    "_native_history_delta": True,
                    "_native_context_bootstrap": native_bootstrap,
                    "_native_conversation": {"logical_id": native_logical_id},
                    "_native_lock_held": True,
                })
            response = await driver.think(
                invocation_id,
                "talk",
                {
                    "name": resident.name,
                    "persona": self.resident_service.read_persona(resident.name),
                    "brain_model": resident.brain_model,
                    "brain_reasoning_effort": resident.brain_reasoning_effort,
                },
                brain_context,
            )
            latest = self._conversation_record(conversation_id)
            if latest.turn_state != "running" or latest.active_invocation_id != invocation_id:
                return
            native_output_marker: str | None = None
            if response.say.strip():
                finished = self._conversation_store.finish_turn(
                    latest,
                    sender=resident.name,
                    text=response.say,
                )
                if finished.messages:
                    native_output_marker = f"cvseq:{finished.messages[-1].seq}"
            else:
                self._conversation_store.finish_without_message(latest)
            # The Nirai transcript commit above is the native-context commit
            # barrier. Clear pending_turn synchronously before any publication
            # await can let the next Conversation turn enter the same logical id.
            if native_logical_id is not None:
                self._native_brain.mark_seen(
                    native_logical_id,
                    resident.brain,
                    native_input_marker,
                    output_entry_id=native_output_marker,
                )
            if response.say.strip() and latest.mode == "talk":
                if public_session_id is None:
                    raise ConversationRuntimeError(
                        "Resident Conversation lost its public Chat Session binding"
                    )
                await self._publish_resident_conversation_reply(
                    resident.name,
                    response.say,
                    session_id=public_session_id,
                )
        except asyncio.CancelledError:
            if driver is not None:
                try:
                    await driver.cancel(invocation_id)
                except Exception:
                    pass
            try:
                current_record = self._conversation_record(conversation_id)
                if (
                    current_record.participant_kind == "resident"
                    and self._native_brain_enabled(
                        self.resident_service.load(current_record.participant).brain
                    )
                ):
                    self._native_brain.reset(
                        self._holo_resident_brain_conversation_id(
                            conversation_id,
                            current_record.participant,
                        )
                    )
            except Exception:
                LOGGER.warning(
                    "resident_conversation_native_cancel_reset_failed conversation_id=%s",
                    conversation_id,
                    exc_info=True,
                )
            try:
                latest = self._conversation_record(conversation_id)
                if latest.turn_state == "running":
                    self._conversation_store.end_turn(
                        latest,
                        "cancelled",
                        error="Conversation turn cancelled",
                    )
            except ConversationRuntimeError:
                pass
            raise
        except (BrainError, ResidentError, ConversationRuntimeError) as exc:
            try:
                latest = self._conversation_record(conversation_id)
                if latest.turn_state == "running":
                    self._conversation_store.end_turn(
                        latest,
                        "failed",
                        error=str(exc) or type(exc).__name__,
                    )
            except ConversationRuntimeError:
                pass
        finally:
            if native_turn_lock_acquired and native_turn_lock is not None:
                native_turn_lock.release()
            self._conversation_invocations.pop(conversation_id, None)
            self._invocation_drivers.pop(invocation_id, None)
            self._conversation_public_sessions.pop(conversation_id, None)
            self._signal_conversation_changed(conversation_id)

    async def holo_conversation_wait_authorized(
        self,
        conversation_id: str,
        *,
        timeout_sec: float,
    ) -> tuple[dict[str, Any], bool]:
        self._holo_authorization.require_attached()
        bounded_timeout = min(
            max(float(timeout_sec), 0.0),
            HOLO_CONVERSATION_WAIT_MAX_SEC,
        )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + bounded_timeout
        event = self._conversation_wait_event(conversation_id)

        # Event is only a wake-up hint. Always re-read the durable turn state
        # after clearing it so a late signal from the previous turn cannot make
        # a newly-started turn look terminal. Clearing before the state read also
        # avoids losing a completion that races with the clear: the terminal
        # durable state is then observed immediately.
        while True:
            event.clear()
            record = self._conversation_record(conversation_id)
            active_task = self._conversation_tasks.get(conversation_id)
            if (
                record.turn_state != "running"
                and (active_task is None or active_task.done())
            ):
                return record.to_protocol(), False
            remaining = deadline - loop.time()
            if remaining <= 0:
                return record.to_protocol(), True
            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                latest = self._conversation_record(conversation_id)
                return latest.to_protocol(), latest.turn_state == "running"

    async def holo_conversation_cancel_authorized(
        self,
        conversation_id: str,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        record = self._conversation_record(conversation_id)
        if record.turn_state != "running":
            return {
                "cancellation_requested": False,
                "conversation": record.to_protocol(),
            }
        cancellation_requested = False
        agent_session_id = self._conversation_agent_sessions.get(conversation_id)
        if agent_session_id is not None:
            try:
                cancellation_requested = (
                    await self.agent_runtime.cancel(agent_session_id)
                ) or cancellation_requested
            except (AgentRuntimeManagerError, AgentSessionStoreError):
                pass
        invocation_id = self._conversation_invocations.get(conversation_id)
        if invocation_id is not None:
            driver = self._invocation_drivers.get(invocation_id)
            if driver is not None:
                try:
                    cancellation_requested = (
                        await driver.cancel(invocation_id)
                    ) or cancellation_requested
                except Exception:
                    pass
        task = self._conversation_tasks.get(conversation_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            cancellation_requested = True
        latest = self._conversation_record(conversation_id)
        if latest.turn_state == "running":
            latest = self._conversation_store.end_turn(
                latest,
                "cancelled",
                error="Conversation turn cancelled",
                agent_session_id=agent_session_id,
            )
        if latest.participant_kind == "provider":
            latest = self._invalidate_provider_conversation_context(latest)
        self._signal_conversation_changed(conversation_id)
        return {
            "cancellation_requested": cancellation_requested,
            "conversation": latest.to_protocol(),
        }

    def holo_conversation_close_authorized(self, conversation_id: str) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        record = self._conversation_record(conversation_id)
        record = self._conversation_store.close(record)
        if record.participant_kind == "provider":
            # Provider-native state is a continuity cache. Cleanup is scheduled
            # off-loop so Windows filesystem retries cannot freeze Core UI/IPC.
            self._schedule_provider_conversation_context_discard(
                record.participant,
                record.conversation_id,
            )
        elif record.participant_kind == "resident":
            try:
                self._native_brain.reset(
                    self._holo_resident_brain_conversation_id(
                        record.conversation_id,
                        record.participant,
                    )
                )
            except BrainError:
                LOGGER.warning(
                    "conversation_resident_context_cleanup_failed conversation_id=%s resident=%s",
                    record.conversation_id,
                    record.participant,
                    exc_info=True,
                )
        self._signal_conversation_changed(conversation_id)
        return record.to_protocol()

    def _chat_session_has_active_conversation(self, session_id: str) -> bool:
        return any(
            bound_session_id == session_id
            for bound_session_id in self._conversation_public_sessions.values()
        )

    def _conversation_task_done(
        self,
        conversation_id: str,
        task: asyncio.Task[None],
    ) -> None:
        if self._conversation_tasks.get(conversation_id) is task:
            self._conversation_tasks.pop(conversation_id, None)
        self._conversation_public_sessions.pop(conversation_id, None)
        if task.cancelled():
            self._signal_conversation_changed(conversation_id)
            return
        error = task.exception()
        if error is not None:
            LOGGER.error(
                "conversation_task_unhandled_failure conversation_id=%s",
                conversation_id,
                exc_info=(type(error), error, error.__traceback__),
            )
            try:
                record = self._conversation_record(conversation_id)
                if record.turn_state == "running":
                    failed = self._conversation_store.end_turn(
                        record,
                        "failed",
                        error=str(error) or type(error).__name__,
                    )
                    if failed.participant_kind == "provider":
                        self._invalidate_provider_conversation_context(failed)
            except ConversationRuntimeError:
                pass
        self._signal_conversation_changed(conversation_id)

    async def _cancel_all_conversations(self) -> None:
        for conversation_id in tuple(self._conversation_tasks):
            try:
                await self.holo_conversation_cancel_authorized(conversation_id)
            except (HoloAuthorizationError, ConversationRuntimeError):
                task = self._conversation_tasks.get(conversation_id)
                if task is not None and not task.done():
                    task.cancel()
        tasks = [task for task in self._conversation_tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @staticmethod
    def _holo_review_verdict(summary: object) -> str:
        if not isinstance(summary, str):
            return "UNKNOWN"
        first = next((line.strip() for line in summary.splitlines() if line.strip()), "")
        normalized = first.upper().replace("_", " ")
        if normalized == "SAFE":
            return "SAFE"
        if normalized == "NEEDS FIX":
            return "NEEDS_FIX"
        return "UNKNOWN"

    def _holo_review_snapshot(self, agent_session_id: str) -> dict[str, Any]:
        payload = self._agent_snapshot_payload(agent_session_id)
        task_id = payload.get("task_id")
        if (
            not isinstance(task_id, str)
            or not task_id.startswith(HOLO_REVIEW_TASK_PREFIX)
            or payload.get("provider") != "cursor"
            or payload.get("origin_chat_session_id") is not None
        ):
            raise HoloAuthorizationError("Agent Session is not a Holo-supervised Cursor review")
        state = payload.get("state")
        final_summary = payload.get("final_summary")
        return {
            "task_id": task_id,
            "agent_session_id": agent_session_id,
            "state": state,
            "terminal": state in TERMINAL_RUN_STATES,
            "target": Path(str(payload.get("working_dir", ""))).name,
            "updated_at": payload.get("updated_at"),
            "verdict": self._holo_review_verdict(final_summary),
            "final_summary": final_summary,
        }

    async def holo_start_cursor_review_authorized(
        self,
        target_name: str,
        prompt: str,
        *,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        cleaned_prompt = prompt.strip()
        if not cleaned_prompt:
            raise AgentRuntimeManagerError("Cursor review prompt must not be empty")
        if len(cleaned_prompt) > TASK_QUEUE_TEXT_LIMIT:
            raise AgentRuntimeManagerError(
                f"Cursor review prompt exceeds the {TASK_QUEUE_TEXT_LIMIT} character limit"
            )
        if self._task_queue_store_error is not None:
            raise AgentRuntimeManagerError(
                f"Task Queue persistence is unavailable: {self._task_queue_store_error}"
            )
        if self._task_work_pending():
            raise AgentRuntimeManagerError(
                "Another Task or Agent Session is already pending; Holo review will not bypass the FIFO boundary"
            )
        if not self.agent_runtime.supports_provider("cursor"):
            raise AgentRuntimeManagerError("Cursor Agent Runtime is not available")

        task_id = f"{HOLO_REVIEW_TASK_PREFIX}{uuid4()}"
        working_dir = self.agent_runtime.workspace_policy.named_review_working_dir(
            target_name,
            task_id=task_id,
        )
        metadata_dir = self.agent_runtime.workspace_policy.task_metadata_dir(task_id)
        snapshot = await self.agent_runtime.start_session(
            task_id=task_id,
            resident=self._holo_resident_name(),
            provider="cursor",
            prompt=cleaned_prompt,
            working_dir=str(working_dir),
            task_metadata_dir=str(metadata_dir),
            model=model,
            reasoning_effort=reasoning_effort,
            origin_chat_session_id=None,
            read_only=True,
            purpose="review",
        )
        return self._holo_review_snapshot(snapshot.agent_session_id)

    async def holo_wait_cursor_review_authorized(
        self,
        agent_session_id: str,
        *,
        timeout_sec: float,
    ) -> tuple[dict[str, Any], bool]:
        self._holo_authorization.require_attached()
        bounded_timeout = min(max(float(timeout_sec), 0.0), HOLO_REVIEW_WAIT_MAX_SEC)
        started = perf_counter()
        while True:
            review = self._holo_review_snapshot(agent_session_id)
            if review["terminal"]:
                return review, False
            elapsed = perf_counter() - started
            if elapsed >= bounded_timeout:
                return review, True
            await asyncio.sleep(min(0.05, bounded_timeout - elapsed))

    async def holo_cancel_cursor_review_authorized(self, agent_session_id: str) -> dict[str, Any]:
        self._holo_authorization.require_attached()
        self._holo_review_snapshot(agent_session_id)
        cancelled = await self.agent_runtime.cancel(agent_session_id)
        return {
            "cancellation_requested": cancelled,
            "review": self._holo_review_snapshot(agent_session_id),
        }

    def _holo_resident_configuration_errors(self) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []
        for name in self.resident_service.enabled_names:
            try:
                resident = self.resident_service.load(name)
                if resident.brain is None:
                    raise ResidentError("Brain provider is not configured")
                provider = self.resident_service.validate_brain_provider(resident.brain)
                self.resident_service.validate_brain_model(resident.brain_model)
                self.resident_service.validate_brain_reasoning_effort(
                    provider,
                    resident.brain_reasoning_effort,
                )
            except ResidentError as exc:
                errors.append({
                    "resident": name,
                    "error": str(exc)[:1000],
                })
        return errors

    def _holo_provider_health(self) -> tuple[dict[str, Any], list[str]]:
        required_by: dict[str, list[str]] = {}
        for name in self.resident_service.enabled_names:
            try:
                resident = self.resident_service.load(name)
            except ResidentError:
                continue
            provider = resident.brain
            if provider is None or provider == HOLO_ADDON_BRAIN:
                continue
            required_by.setdefault(provider, []).append(resident.name)

        statuses: dict[str, Any] = {}
        missing: list[str] = []
        for provider, residents in sorted(required_by.items()):
            available = False
            detail = ""
            try:
                if provider == "cursor":
                    command = resolve_cursor_command()
                    available = True
                    detail = " ".join(command)
                elif provider == "codex":
                    command = resolve_codex_command()
                    available = True
                    detail = " ".join(command)
                elif provider == "gemini":
                    available = load_gemini_api_key(self.config.root) is not None
                    detail = "world/.env GEMINI_API_KEY" if available else "GEMINI_API_KEY missing"
                elif provider == "claude-code":
                    # Current Nirai acceptance intentionally keeps Claude disabled.
                    detail = "disabled by current Nirai acceptance"
                else:
                    detail = "unsupported Brain provider"
            except (BrainUnavailableError, OSError, RuntimeError) as exc:
                detail = f"{type(exc).__name__}: {exc}"
            if not available:
                missing.append(provider)
            statuses[provider] = {
                "status": "ok" if available else "unavailable",
                "required_by": residents,
                "detail": detail[:1000],
            }
        return statuses, missing

    def _holo_health_snapshot(self) -> dict[str, Any]:
        # A Dive is also a cheap repair checkpoint. Cross-store Memory replay is
        # idempotent, so retry it here before reporting outstanding health.
        self._reconcile_memory_outbox(max_batches=1, batch_size=32)
        try:
            memory_outbox_pending = self.sessions.store.pending_memory_sync_count()
        except ChatStoreError:
            memory_outbox_pending = -1

        interrupted_agent_sessions = sum(
            snapshot.run_state == "interrupted"
            for snapshot in self.agent_runtime.list_snapshots()
        )
        incident_store_available = self.incidents is not None
        incident_fallback_pending = False
        unresolved_count = 0
        recent_incidents: list[dict[str, Any]] = []
        if self.incidents is not None:
            try:
                if self.incidents.fallback_pending():
                    try:
                        self.incidents.replay_fallback()
                    except IncidentStoreError:
                        # The fallback journal itself is the durable evidence.
                        # Keep Health in attention state and retry on the next Dive.
                        pass
                incident_fallback_pending = self.incidents.fallback_pending()
                unresolved_count = self.incidents.unresolved_count()
                for incident in self.incidents.unresolved(limit=5):
                    recent_incidents.append({
                        "incident_id": incident["incident_id"],
                        "severity": incident["severity"],
                        "component": incident["component"],
                        "code": incident["code"],
                        "summary": incident["summary"],
                        "last_seen": incident["last_seen"],
                        "occurrence_count": incident["occurrence_count"],
                    })
            except IncidentStoreError:
                incident_store_available = False

        resident_configuration_errors = self._holo_resident_configuration_errors()
        provider_runtime, missing_required_providers = self._holo_provider_health()
        attention = (
            not incident_store_available
            or incident_fallback_pending
            or memory_outbox_pending != 0
            or interrupted_agent_sessions > 0
            or unresolved_count > 0
            or bool(missing_required_providers)
            or bool(resident_configuration_errors)
        )
        return {
            "status": "attention" if attention else "ok",
            "incident_store_available": incident_store_available,
            "incident_fallback_pending": incident_fallback_pending,
            "unresolved_incident_count": unresolved_count,
            "memory_outbox_pending": memory_outbox_pending,
            "interrupted_agent_sessions": interrupted_agent_sessions,
            "provider_runtime": provider_runtime,
            "missing_required_providers": missing_required_providers,
            "resident_configuration_errors": resident_configuration_errors,
            "recent_incidents": recent_incidents,
        }

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
            "event_epoch": self._holo_events.event_epoch,
            "health": self._holo_health_snapshot(),
        }

    async def holo_wait_events(
        self,
        after_event_id: int,
        *,
        timeout_sec: float,
        limit: int = 50,
        event_epoch: str | None = None,
    ) -> HoloEventWaitResult:
        """Wait for allowlisted semantic events after a known cursor."""
        return await self._holo_events.wait_after(
            after_event_id,
            timeout_sec=timeout_sec,
            limit=limit,
            event_epoch=event_epoch,
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
        session_id: str | None = None,
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

        entry = self.sessions.append_holo_say(
            cleaned,
            to=to,
            sender=self._holo_resident_name(),
            session_id=session_id,
        )
        self._record_public_memory_entry(entry)
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
        if self._world_memory_worker is not None:
            self._world_memory_worker_task = asyncio.create_task(
                self._world_memory_worker_loop(),
                name="world-memory-background",
            )
        if self._private_memory_worker is not None:
            self._private_memory_worker_task = asyncio.create_task(
                self._private_memory_worker_loop(),
                name="private-memory-background",
            )
        self._schedule_task_queue_dispatch()

    async def stop(self) -> None:
        if self._server is None:
            return
        memory_task = self._world_memory_worker_task
        if memory_task is not None and not memory_task.done():
            memory_task.cancel()
            await asyncio.gather(memory_task, return_exceptions=True)
        self._world_memory_worker_task = None
        private_memory_task = self._private_memory_worker_task
        if private_memory_task is not None and not private_memory_task.done():
            private_memory_task.cancel()
            await asyncio.gather(private_memory_task, return_exceptions=True)
        self._private_memory_worker_task = None
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
        await self._cancel_all_conversations()
        if self._provider_context_cleanup_tasks:
            await asyncio.gather(
                *tuple(self._provider_context_cleanup_tasks.values()),
                return_exceptions=True,
            )
            self._provider_context_cleanup_tasks.clear()
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
        orphaned_terminal_results = 0
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
        if orphaned_terminal_results:
            LOGGER.info(
                "agent_task_recovery_orphans_skipped count=%s",
                orphaned_terminal_results,
            )

    async def _broadcast_agent_event(self, event: AgentEvent) -> None:
        task_update, chat_entry = await self._handle_agent_task_event(event)
        terminal_update = (
            task_update is not None
            and task_update.get("phase") in {"done", "failed", "cancelled"}
        )
        terminal_run_state = (
            event.type == "run_state"
            and event.payload.get("state") in TERMINAL_RUN_STATES
        )
        if terminal_update:
            self._recovered_agent_notifications[event.agent_session_id] = (
                dict(task_update),
                chat_entry,
            )
        if terminal_run_state:
            # Holo-supervised reviews have no origin Chat Session and therefore
            # no task_update, but they still occupy the single Agent slot. A
            # normal FIFO Task queued while a review is running must resume as
            # soon as that review reaches a terminal state.
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
                    self._record_public_memory_entry(chat_entry)
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
        payload = self.agent_runtime.snapshot_payload(
            agent_session_id,
            event_limit=AGENT_SNAPSHOT_EVENT_LIMIT,
        )
        session = payload["session"]
        events = payload["events"]
        first_event_seq = next(
            (
                int(event["seq"])
                for event in events
                if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool)
            ),
            None,
        )
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
            "recovery_options": payload.get("recovery_options", []),
            "events": events,
            "events_truncated": first_event_seq is not None and first_event_seq > 1,
            "event_window_start_seq": first_event_seq,
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
                    resident_name=record.resident_name,
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
            or self._task_queue_store_error is not None
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
        resident_name: str | None = None,
    ) -> None:
        origin_session_id = origin_session_id or self.sessions.active_session_id
        try:
            if resident_name is None:
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

            assignment_policy = "direct"
            if resident_name is not None:
                resident = self._resolve_direct_task_resident(resident_name)
            else:
                assignment_policy = "first_eligible_volunteer"
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
                (
                    f"{resident.name}へ直接Taskを割り当てました"
                    if assignment_policy == "direct"
                    else f"{resident.name}が最初の有資格立候補者として担当に決まりました"
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
                "Taskを停止しました" if resident_name is not None else "Task相談を停止しました",
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
                    {
                        "ok": True,
                        "dive_session_id": binding.dive_session_id,
                        "health": self._holo_health_snapshot(),
                    },
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
            if message_type == "holo_incidents_request":
                limit = payload.get("limit", 20)
                if not isinstance(limit, int) or isinstance(limit, bool):
                    raise IncidentStoreError("Incident limit must be an integer")
                incidents = self.holo_incidents_authorized(limit=limit)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "incidents",
                    {"ok": True, **incidents},
                )
                return
            if message_type == "holo_incident_resolve_request":
                incident_id = payload.get("incident_id")
                note = payload.get("note", "")
                if not isinstance(incident_id, str) or not incident_id.strip():
                    raise IncidentStoreError("incident_id is required")
                if not isinstance(note, str):
                    raise IncidentStoreError("Incident resolution note must be a string")
                resolved = self.holo_resolve_incident_authorized(incident_id, note)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "incident_resolve",
                    {"ok": True, "incident_id": incident_id, "resolved": resolved},
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
                event_epoch = payload.get("event_epoch")
                if not isinstance(after_event_id, int) or isinstance(after_event_id, bool):
                    raise ValueError("after_event_id must be an integer")
                if not isinstance(timeout_sec, (int, float)) or isinstance(timeout_sec, bool):
                    raise ValueError("timeout_sec must be a number")
                if not isinstance(limit, int) or isinstance(limit, bool):
                    raise ValueError("limit must be an integer")
                if event_epoch is not None and not isinstance(event_epoch, str):
                    raise ValueError("event_epoch must be a string when provided")

                wait_task = asyncio.create_task(
                    self.holo_wait_events_authorized(
                        after_event_id,
                        timeout_sec=float(timeout_sec),
                        limit=limit,
                        event_epoch=event_epoch,
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
                        "next_event_id": result.next_event_id,
                        "high_watermark_event_id": result.high_watermark_event_id,
                        "event_epoch": result.event_epoch,
                        "cursor_reset": result.cursor_reset,
                        "gap_detected": result.gap_detected,
                        "timed_out": result.timed_out,
                    },
                )
                return
            if message_type == "holo_conversation_start_request":
                participant_kind = payload.get("participant_kind")
                participant = payload.get("participant")
                mode = payload.get("mode")
                target = payload.get("target")
                model = payload.get("model")
                reasoning_effort = payload.get("reasoning_effort")
                if not isinstance(participant_kind, str):
                    raise ValueError("Conversation participant_kind must be a string")
                if not isinstance(participant, str):
                    raise ValueError("Conversation participant must be a string")
                if not isinstance(mode, str):
                    raise ValueError("Conversation mode must be a string")
                if target is not None and not isinstance(target, str):
                    raise ValueError("Conversation target must be a string")
                if model is not None and not isinstance(model, str):
                    raise ValueError("Conversation model must be a string")
                if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                    raise ValueError("Conversation reasoning effort must be a string")
                conversation = self.holo_conversation_start_authorized(
                    participant_kind,
                    participant,
                    mode,
                    target_name=target,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "conversation_start",
                    {"ok": True, "conversation": conversation},
                )
                return
            if message_type == "holo_conversation_send_request":
                conversation_id = payload.get("conversation_id")
                text = payload.get("text")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError("Conversation id is required")
                if not isinstance(text, str):
                    raise ValueError("Conversation message must be a string")
                conversation = await self.holo_conversation_send_authorized(
                    conversation_id,
                    text,
                )
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "conversation_send",
                    {"ok": True, "conversation": conversation},
                )
                return
            if message_type == "holo_conversation_wait_request":
                conversation_id = payload.get("conversation_id")
                timeout_sec = payload.get("timeout_sec")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError("Conversation id is required")
                if not isinstance(timeout_sec, (int, float)) or isinstance(timeout_sec, bool):
                    raise ValueError("Conversation timeout_sec must be a number")
                wait_task = asyncio.create_task(
                    self.holo_conversation_wait_authorized(
                        conversation_id,
                        timeout_sec=float(timeout_sec),
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
                conversation, timed_out = await wait_task
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "conversation_wait",
                    {
                        "ok": True,
                        "conversation": conversation,
                        "timed_out": timed_out,
                    },
                )
                return
            if message_type == "holo_conversation_cancel_request":
                conversation_id = payload.get("conversation_id")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError("Conversation id is required")
                result = await self.holo_conversation_cancel_authorized(conversation_id)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "conversation_cancel",
                    {"ok": True, **result},
                )
                return
            if message_type == "holo_conversation_close_request":
                conversation_id = payload.get("conversation_id")
                if not isinstance(conversation_id, str) or not conversation_id:
                    raise ValueError("Conversation id is required")
                conversation = self.holo_conversation_close_authorized(conversation_id)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "conversation_close",
                    {"ok": True, "conversation": conversation},
                )
                return
            if message_type == "holo_cursor_review_start_request":
                target = payload.get("target")
                prompt = payload.get("prompt")
                model = payload.get("model")
                reasoning_effort = payload.get("reasoning_effort")
                if not isinstance(target, str) or not target.strip():
                    raise ValueError("Cursor review target must be a non-empty folder name")
                if not isinstance(prompt, str):
                    raise ValueError("Cursor review prompt must be a string")
                if model is not None and not isinstance(model, str):
                    raise ValueError("Cursor review model must be a string")
                if reasoning_effort is not None and not isinstance(reasoning_effort, str):
                    raise ValueError("Cursor review reasoning effort must be a string")
                review = await self.holo_start_cursor_review_authorized(
                    target,
                    prompt,
                    model=model,
                    reasoning_effort=reasoning_effort,
                )
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "cursor_review_start",
                    {"ok": True, "review": review},
                )
                return
            if message_type == "holo_cursor_review_wait_request":
                agent_session_id = payload.get("agent_session_id")
                timeout_sec = payload.get("timeout_sec")
                if not isinstance(agent_session_id, str) or not agent_session_id:
                    raise ValueError("Cursor review agent_session_id is required")
                if not isinstance(timeout_sec, (int, float)) or isinstance(timeout_sec, bool):
                    raise ValueError("Cursor review timeout_sec must be a number")
                wait_task = asyncio.create_task(
                    self.holo_wait_cursor_review_authorized(
                        agent_session_id,
                        timeout_sec=float(timeout_sec),
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
                review, timed_out = await wait_task
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "cursor_review_wait",
                    {"ok": True, "review": review, "timed_out": timed_out},
                )
                return
            if message_type == "holo_cursor_review_cancel_request":
                agent_session_id = payload.get("agent_session_id")
                if not isinstance(agent_session_id, str) or not agent_session_id:
                    raise ValueError("Cursor review agent_session_id is required")
                result = await self.holo_cancel_cursor_review_authorized(agent_session_id)
                await self._send_holo_local_result(
                    websocket,
                    message_id,
                    "cursor_review_cancel",
                    {"ok": True, **result},
                )
                return
            raise HoloAuthorizationError("Unsupported Holo local operation")
        except (
            AgentRuntimeManagerError,
            AgentSafetyError,
            AgentSessionStoreError,
            ChatStoreError,
            ConversationRuntimeError,
            HoloAuthorizationError,
            IncidentStoreError,
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
                        LOGGER.warning("hello_role_rejected role=%r", role)
                        await websocket.close(code=4004, reason="Unsupported Nirai hello role")
                        return
                    secret = payload.get("secret")
                    if (
                        not isinstance(secret, str)
                        or not hmac.compare_digest(secret, self._world_secret)
                    ):
                        LOGGER.warning("world_auth_rejected")
                        await websocket.close(code=4003, reason="World authentication failed")
                        return
                    try:
                        protocol_version, runtime_id, capabilities = parse_runtime_descriptor(
                            payload.get("protocol")
                        )
                    except ProtocolError as exc:
                        LOGGER.warning("world_protocol_rejected error=%s", exc)
                        await websocket.close(code=4004, reason="Nirai World protocol metadata is required")
                        return
                    if protocol_version != PROTOCOL_VERSION:
                        LOGGER.warning(
                            "world_protocol_version_rejected world=%s core=%s runtime_id=%s",
                            protocol_version,
                            PROTOCOL_VERSION,
                            runtime_id,
                        )
                        await websocket.close(
                            code=4004,
                            reason=f"Unsupported Nirai protocol version {protocol_version}; Core requires {PROTOCOL_VERSION}",
                        )
                        return
                    previous = self._world_connection
                    if previous is not None and previous is not websocket:
                        await previous.close(code=4000, reason="replaced by newer world connection")
                    self._world_connection = websocket
                    self._world_runtime = (runtime_id, capabilities)
                    LOGGER.info(
                        "world_connected runtime_id=%s capabilities=%s",
                        runtime_id,
                        ",".join(capabilities),
                    )
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
                        if self._chat_session_has_active_conversation(session_id):
                            raise ChatStoreError("Holo Conversation応答中のチャット履歴は削除できません")
                        active_session = self.sessions.delete_session(session_id)
                        self._native_brain.reset_prefix(f"chat:{session_id}:")
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
                        if self._chat_session_has_active_conversation(session_id):
                            raise ChatStoreError("Holo Conversation応答中のWorld Memoryは変更できません")
                        if not self.sessions.store.has_session(session_id):
                            raise ChatStoreError(f"unknown chat session: {session_id}")
                        deleted_count = self.world_memory.forget_session(session_id)
                        active_session = self.sessions.delete_session(session_id)
                        self._native_brain.reset_prefix(f"chat:{session_id}:")
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
                        self._record_public_memory_entry(entry)
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
                        self._record_private_memory_entry(resident_name, entry)
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
                        resident_value = payload.get("resident")
                        if resident_value is not None and (
                            not isinstance(resident_value, str) or not resident_value.strip()
                        ):
                            raise AgentRuntimeManagerError(
                                "Task resident name must be a non-empty string"
                            )
                        resident_name = None
                        if isinstance(resident_value, str):
                            resident_name = self._resolve_direct_task_resident(resident_value).name
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
                            resident_name=resident_name,
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
                                    **({"assigned_resident": resident_name, "assignment_policy": "direct"} if resident_name is not None else {}),
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
                    elif message_type == "agent_session_recover":
                        agent_session_id = payload.get("agent_session_id")
                        action = payload.get("action")
                        if not isinstance(agent_session_id, str) or not agent_session_id:
                            raise AgentRuntimeManagerError("agent_session_id is required")
                        if action not in {"resume", "rerun", "abandon"}:
                            raise AgentRuntimeManagerError("Agent recovery action is invalid")
                        recovered = await self.agent_runtime.recover_session(agent_session_id, action)
                        await websocket.send(make_message(
                            "agent_session_recovery_result",
                            {
                                "source_agent_session_id": agent_session_id,
                                "agent_session_id": recovered.agent_session_id,
                                "action": action,
                            },
                            message.get("id"),
                        ))
                        await self._send_agent_snapshot(
                            websocket,
                            recovered.agent_session_id,
                            message.get("id"),
                        )
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
                        self._native_brain.reset_resident(name)
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
                        self._native_brain.reset_resident(name)
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
                self._world_runtime = None
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
            driver = NativeConversationBrainDriver(
                provider,
                self._native_brain,
                lambda: CodexDriver(self.config.root),
            )
        elif provider == "claude-code":
            driver = ClaudeCodeDriver(self.config.root)
        elif provider == "cursor":
            driver = NativeConversationBrainDriver(
                provider,
                self._native_brain,
                lambda: CursorDriver(self.config.root),
            )
        elif provider == "gemini":
            driver = GeminiDriver(
                self.config.root,
                native_conversation_service=self._native_brain,
            )
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

    def _native_brain_enabled(self, provider: str | None) -> bool:
        return (
            self._brain_driver_override is None
            and self._native_brain.supports(provider)
        )

    @staticmethod
    def _public_brain_conversation_id(session_id: str, resident_name: str) -> str:
        return f"chat:{session_id}:public:{resident_name}"

    @staticmethod
    def _whisper_brain_conversation_id(resident_name: str) -> str:
        # Whisper is a Resident-scoped Private Channel, not a child of the
        # currently selected public Chat Session. The public session id remains
        # provenance metadata on each raw Whisper entry only.
        return f"private:whisper:{resident_name}"

    @staticmethod
    def _holo_resident_brain_conversation_id(
        conversation_id: str,
        resident_name: str,
    ) -> str:
        return f"holo:{conversation_id}:resident:{resident_name}"

    def _native_public_history(
        self,
        session_id: str,
        resident_name: str,
        provider: str,
    ) -> tuple[list[dict[str, Any]], str, str | None, bool]:
        logical_id = self._public_brain_conversation_id(session_id, resident_name)
        bootstrap = not self._native_brain.has_compatible_state(logical_id, provider)
        last_seen = self._native_brain.last_seen_entry_id(logical_id, provider)
        last_output = self._native_brain.last_output_entry_id(logical_id, provider)
        if not bootstrap and isinstance(last_seen, str):
            try:
                history = self.sessions.public_history_after(session_id, last_seen, limit=101)
            except ChatStoreError:
                LOGGER.warning(
                    "native_brain_public_marker_missing session_id=%s resident=%s provider=%s reset=true",
                    session_id,
                    resident_name,
                    provider,
                )
                self._native_brain.reset(logical_id)
                bootstrap = True
                history = self.sessions.public_history(session_id, limit=20)
        else:
            # A rebuilt Provider context starts from a bounded recent tail. Long-
            # term continuity comes from Nirai Memory, not by materializing the
            # entire lifetime chat log before applying a limit.
            history = self.sessions.public_history(session_id, limit=20)
        if last_output is not None:
            history = [item for item in history if item.get("entry_id") != last_output]
        if len(history) > 100 or sum(len(str(item.get("text", ""))) for item in history) > 64_000:
            LOGGER.warning(
                "native_brain_public_delta_oversize session_id=%s resident=%s provider=%s reset=true count=%s",
                session_id,
                resident_name,
                provider,
                len(history),
            )
            self._native_brain.reset(logical_id)
            bootstrap = True
            history = self.sessions.public_history(session_id, limit=20)
        marker = history[-1].get("entry_id") if history else last_seen
        return history, logical_id, marker if isinstance(marker, str) else None, bootstrap

    def _native_whisper_history(
        self,
        resident_name: str,
        provider: str,
    ) -> tuple[list[dict[str, Any]], str, str | None, bool]:
        logical_id = self._whisper_brain_conversation_id(resident_name)
        bootstrap = not self._native_brain.has_compatible_state(logical_id, provider)
        last_seen = self._native_brain.last_seen_entry_id(logical_id, provider)
        last_output = self._native_brain.last_output_entry_id(logical_id, provider)
        if not bootstrap and isinstance(last_seen, str):
            try:
                history = self.private_memory.whispers_after(resident_name, last_seen, limit=101)
            except PrivateMemoryError:
                LOGGER.warning(
                    "native_brain_whisper_marker_missing resident=%s provider=%s reset=true",
                    resident_name,
                    provider,
                )
                self._native_brain.reset(logical_id)
                bootstrap = True
                history = self.private_memory.recent_whispers(resident_name, 20)
        else:
            # A rebuilt provider Working Context never receives years of Raw
            # Whisper. Bootstrap from bounded recent Raw; long-term continuity
            # belongs to Nirai Private Memory / Retrieval rather than transport.
            history = self.private_memory.recent_whispers(resident_name, 20)
        if last_output is not None:
            history = [item for item in history if item.get("entry_id") != last_output]
        if len(history) > 100 or sum(len(str(item.get("text", ""))) for item in history) > 64_000:
            LOGGER.warning(
                "native_brain_whisper_delta_oversize resident=%s provider=%s reset=true count=%s",
                resident_name,
                provider,
                len(history),
            )
            self._native_brain.reset(logical_id)
            bootstrap = True
            history = self.private_memory.recent_whispers(resident_name, 20)
        marker = history[-1].get("entry_id") if history else last_seen
        return history, logical_id, marker if isinstance(marker, str) else None, bootstrap

    async def _notify_memory_fallback(
        self,
        session_id: str,
        memory_scope: str,
        reason: str,
    ) -> None:
        reason_labels = {
            "semantic_unavailable": "Gemini Embedding / Semantic index unavailable",
            "vector_index_empty": "Semantic vector index is empty",
            "query_embedding_budget_exhausted": "Gemini query embedding budget exhausted",
            "semantic_query_failed": "Gemini semantic query failed",
        }
        detail = reason_labels.get(reason, reason)
        text = (
            f"Semantic Memory fallback: {memory_scope} は Local FTS で想起しました。"
            f"理由: {detail}。"
        )
        try:
            entry = self.sessions.append_system(session_id, text)
        except Exception:
            LOGGER.warning(
                "memory_fallback_system_entry_failed session_id=%s scope=%s reason=%s",
                session_id,
                memory_scope,
                reason,
                exc_info=True,
            )
            return
        websocket = self._world_connection
        if websocket is None:
            return
        try:
            await websocket.send(make_message("chat_entry", entry))
        except Exception:
            # The entry is already durable in the target Chat Session. A later
            # World reconnect/history load will still show the degradation.
            LOGGER.warning(
                "memory_fallback_system_broadcast_failed session_id=%s scope=%s reason=%s",
                session_id,
                memory_scope,
                reason,
                exc_info=True,
            )

    async def _world_memory_context(
        self,
        query: str,
        *,
        recent_public_entries: list[dict[str, Any]],
        session_id: str | None = None,
    ) -> list[dict[str, object]]:
        if not query.strip():
            return []
        top_k = WorldMemoryHybridRetriever.DEFAULT_TOP_K
        excluded_raw_ids = {
            WorldMemoryService.raw_entry_id(entry)
            for entry in recent_public_entries
        }
        contexts: list[dict[str, object]] = []
        try:
            fallback_notifier = (
                (lambda reason: self._notify_memory_fallback(session_id, "World Memory", reason))
                if session_id is not None and self.config.memory.world_processor == "gemini"
                else None
            )
            hits = await self.world_hybrid_retriever.search(
                query,
                top_k=top_k,
                exclude_raw_ids=excluded_raw_ids,
                on_fallback=fallback_notifier,
            )
            contexts.extend(hit.to_context() for hit in hits)
        except WorldMemoryRecallError:
            LOGGER.warning("world_memory_hybrid_retrieval_failed", exc_info=True)

        if len(contexts) >= top_k:
            return contexts[:top_k]

        # Old Episode FTS remains only for legacy sessions that have no Raw
        # rows yet. It must never resurrect a weak/superseded memory from a
        # session already governed by the new Raw/Structured retriever.
        excluded_markers = {
            WorldMemoryService.entry_marker(entry)
            for entry in recent_public_entries
        }
        try:
            legacy_hits = self.world_retriever.search(
                query,
                top_k=top_k,
                exclude_entry_markers=excluded_markers,
            )
            for hit in legacy_hits:
                if (
                    self.world_memory.has_raw_session(hit.session_id)
                    or self.world_memory.is_session_forgotten(hit.session_id)
                ):
                    continue
                contexts.append(hit.to_context())
                if len(contexts) >= top_k:
                    break
        except (WorldMemoryRetrieverError, WorldMemoryError):
            LOGGER.warning("world_memory_legacy_retrieval_failed", exc_info=True)
        return contexts[:top_k]

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

        try:
            current_public_history = self.sessions.public_history(session_id, limit=20)
            memory_query = current_public_history[-1].get("text", "") if current_public_history else ""
            world_memories = await self._world_memory_context(
                memory_query if isinstance(memory_query, str) else "",
                recent_public_entries=current_public_history,
                session_id=session_id,
            )
        except Exception as exc:
            # response_state=true is already visible to World. Any preparation
            # failure must terminate that request explicitly or ChatBar remains
            # blocked forever waiting for a response that will never run.
            try:
                await websocket.send(make_message("notice", {
                    "level": "WARN",
                    "text": f"応答準備に失敗しました: {str(exc) or type(exc).__name__}",
                }))
            finally:
                await websocket.send(make_message("response_state", {
                    "active": False,
                    "request_id": request_id,
                    "session_id": session_id,
                }))
            raise

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
                native_logical_id: str | None = None
                native_input_marker: str | None = None
                native_turn_lock: asyncio.Lock | None = None
                native_turn_lock_acquired = False
                try:
                    driver = self._get_brain_driver(resident.brain)
                    self._invocation_drivers[invocation_id] = driver
                    if request_id in self._cancelled_requests:
                        break
                    brain_context: dict[str, Any] = {
                        "history": self.sessions.public_history(session_id, limit=20),
                        "world_memories": world_memories,
                        "current_residents": list(self.resident_service.enabled_names),
                    }
                    if self._native_brain_enabled(resident.brain):
                        native_turn_lock = self._native_brain.conversation_lock(
                            self._public_brain_conversation_id(session_id, resident.name)
                        )
                        await native_turn_lock.acquire()
                        native_turn_lock_acquired = True
                        (
                            native_history,
                            native_logical_id,
                            native_input_marker,
                            native_bootstrap,
                        ) = self._native_public_history(
                            session_id,
                            resident.name,
                            resident.brain,
                        )
                        brain_context.update({
                            "history": native_history,
                            "_native_history_delta": True,
                            "_native_context_bootstrap": native_bootstrap,
                            "_native_conversation": {"logical_id": native_logical_id},
                            "_native_lock_held": True,
                        })
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
                        brain_context,
                    )
                    if request_id in self._cancelled_requests:
                        LOGGER.info(
                            "brain_result_discarded_cancelled request_id=%s invocation_id=%s resident=%s",
                            request_id,
                            invocation_id,
                            resident.name,
                        )
                        if native_logical_id is not None:
                            self._native_brain.reset(native_logical_id)
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
                    native_output_marker: str | None = None
                    entry: dict[str, Any] | None = None
                    if response.say:
                        entry = self.sessions.append_resident_say(
                            session_id,
                            resident.name,
                            response.say,
                            request_id,
                        )
                        native_output_marker = entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None
                        self._record_public_memory_entry(entry)
                    # The local Chat/World Memory commit is durable enough to
                    # advance native continuity. Do it before transport awaits.
                    if native_logical_id is not None:
                        self._native_brain.mark_seen(
                            native_logical_id,
                            resident.brain,
                            native_input_marker,
                            output_entry_id=native_output_marker,
                        )
                    if entry is not None:
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
                    if native_turn_lock_acquired and native_turn_lock is not None:
                        native_turn_lock.release()
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
        native_turn_lock: asyncio.Lock | None = None
        native_turn_lock_acquired = False
        try:
            if request_id in self._cancelled_requests:
                return
            private_context = self.private_memory.context_for_brain(resident.name, session_id)
            current_whisper_history = self.sessions.whisper_history(
                session_id,
                resident.name,
                limit=20,
            )
            native_logical_id: str | None = None
            native_input_marker: str | None = None
            native_bootstrap = False
            if self._native_brain_enabled(resident.brain):
                native_turn_lock = self._native_brain.conversation_lock(
                    self._whisper_brain_conversation_id(resident.name)
                )
                await native_turn_lock.acquire()
                native_turn_lock_acquired = True
                (
                    current_whisper_history,
                    native_logical_id,
                    native_input_marker,
                    native_bootstrap,
                ) = self._native_whisper_history(
                    resident.name,
                    resident.brain,
                )
            memory_query = (
                current_whisper_history[-1].get("text", "")
                if current_whisper_history
                else ""
            )
            current_public_history = self.sessions.public_history(session_id, limit=20)
            world_memories = await self._world_memory_context(
                memory_query if isinstance(memory_query, str) else "",
                recent_public_entries=current_public_history,
                session_id=session_id,
            )
            recent_private_entries = private_context.get("recent_whispers")
            excluded_private_entry_ids = {
                str(entry["entry_id"])
                for entry in (
                    recent_private_entries if isinstance(recent_private_entries, list) else []
                )
                if isinstance(entry, dict)
                and isinstance(entry.get("entry_id"), str)
            }
            private_memories = [
                hit.to_context(resident.name)
                for hit in await self.private_hybrid_retriever.search(
                    resident.name,
                    memory_query if isinstance(memory_query, str) else "",
                    top_k=4,
                    exclude_entry_ids=excluded_private_entry_ids,
                    on_fallback=(
                        (
                            lambda reason: self._notify_memory_fallback(
                                session_id,
                                f"Private Memory ({resident.name})",
                                reason,
                            )
                        )
                        if self.config.memory.private_semantic_provider == "gemini"
                        else None
                    ),
                )
            ]
            driver = self._get_brain_driver(resident.brain)
            self._invocation_drivers[invocation_id] = driver
            brain_context: dict[str, Any] = {
                **private_context,
                "world_memories": world_memories,
                "private_memories": private_memories,
                "current_residents": list(self.resident_service.enabled_names),
                "public_history": self.sessions.public_history(session_id, limit=20),
                "current_whisper_history": current_whisper_history,
            }
            if native_logical_id is not None:
                # The native Whisper session already remembers prior Whisper
                # turns. Do not keep replaying raw private/public tails into it;
                # current Structured Private Context and retrieved World Memory
                # remain explicit Nirai-owned context until Memory redesign.
                brain_context.update({
                    "recent_whispers": [],
                    "public_history": [],
                    "current_whisper_history": current_whisper_history,
                    "_native_history_delta": True,
                    "_native_context_bootstrap": native_bootstrap,
                    "_native_conversation": {"logical_id": native_logical_id},
                    "_native_lock_held": True,
                })
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
                brain_context,
            )
            if request_id in self._cancelled_requests:
                LOGGER.info(
                    "brain_result_discarded_cancelled request_id=%s invocation_id=%s",
                    request_id,
                    invocation_id,
                )
                if native_logical_id is not None:
                    self._native_brain.reset(native_logical_id)
                return
            LOGGER.info(
                "brain_success request_id=%s invocation_id=%s elapsed_ms=%d has_say=%s pass=%s mode=whisper",
                request_id,
                invocation_id,
                round((perf_counter() - started_at) * 1000),
                bool(response.say),
                response.passed,
            )
            native_output_marker: str | None = None
            entry: dict[str, Any] | None = None
            if response.say:
                entry = self.sessions.append_resident_whisper(
                    session_id,
                    resident.name,
                    response.say,
                    request_id,
                )
                native_output_marker = (
                    entry.get("entry_id")
                    if isinstance(entry.get("entry_id"), str)
                    else None
                )
                self._record_private_memory_entry(resident.name, entry)
            # Raw Private Memory and Chat entry are committed before exposing
            # the turn as transport-visible. Advance native continuity first.
            if native_logical_id is not None:
                self._native_brain.mark_seen(
                    native_logical_id,
                    resident.brain,
                    native_input_marker,
                    output_entry_id=native_output_marker,
                )
            if entry is not None:
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
            if native_turn_lock_acquired and native_turn_lock is not None:
                native_turn_lock.release()
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
                native_logical_id: str | None = None
                native_input_marker: str | None = None
                native_turn_lock: asyncio.Lock | None = None
                native_turn_lock_acquired = False
                try:
                    assert speaker.brain is not None
                    driver = self._get_brain_driver(speaker.brain)
                    self._invocation_drivers[invocation_id] = driver
                    self._resident_chat_invocations.add(invocation_id)
                    current_public_history = self.sessions.public_history(
                        target_session_id,
                        limit=20,
                    )
                    brain_history = current_public_history
                    native_bootstrap = False
                    if self._native_brain_enabled(speaker.brain):
                        native_turn_lock = self._native_brain.conversation_lock(
                            self._public_brain_conversation_id(target_session_id, speaker.name)
                        )
                        await native_turn_lock.acquire()
                        native_turn_lock_acquired = True
                        (
                            brain_history,
                            native_logical_id,
                            native_input_marker,
                            native_bootstrap,
                        ) = self._native_public_history(
                            target_session_id,
                            speaker.name,
                            speaker.brain,
                        )
                    latest_public_text = (
                        current_public_history[-1].get("text", "")
                        if current_public_history
                        else ""
                    )
                    brain_context: dict[str, Any] = {
                        "history": brain_history,
                        "world_memories": await self._world_memory_context(
                            str(latest_public_text),
                            recent_public_entries=current_public_history,
                            session_id=target_session_id,
                        ),
                        "current_residents": list(self.resident_service.enabled_names),
                        "conversation_kind": "resident_chat",
                        "participants": list(participants),
                        "previous_speaker": previous_speaker,
                        "addressed_to": addressed_to,
                    }
                    if native_logical_id is not None:
                        brain_context.update({
                            "_native_history_delta": True,
                            "_native_context_bootstrap": native_bootstrap,
                            "_native_conversation": {"logical_id": native_logical_id},
                            "_native_lock_held": True,
                        })
                    response = await driver.think(
                        invocation_id,
                        "talk",
                        {
                            "name": speaker.name,
                            "persona": self.resident_service.read_persona(speaker.name),
                            "brain_model": speaker.brain_model,
                            "brain_reasoning_effort": speaker.brain_reasoning_effort,
                        },
                        brain_context,
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
                    native_output_marker: str | None = None
                    entry: dict[str, Any] | None = None
                    if response.say:
                        entry = self.sessions.append_resident_chat(
                            target_session_id,
                            speaker.name,
                            effective_to,
                            response.say,
                        )
                        native_output_marker = (
                            entry.get("entry_id")
                            if isinstance(entry.get("entry_id"), str)
                            else None
                        )
                        entries.append(entry)
                    if native_logical_id is not None:
                        self._native_brain.mark_seen(
                            native_logical_id,
                            speaker.brain,
                            native_input_marker,
                            output_entry_id=native_output_marker,
                        )
                    if entry is not None:
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
                    if native_turn_lock_acquired and native_turn_lock is not None:
                        native_turn_lock.release()
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
        self._record_public_memory_entry(entry)
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
                    "protocol": runtime_descriptor(CORE_RUNTIME_ID, CORE_CAPABILITIES),
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
