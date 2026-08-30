from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from websockets.asyncio.server import ServerConnection, serve

from .brains.base import BrainDriver, BrainError, BrainUnavailableError
from .brains.claude_code import ClaudeCodeDriver
from .brains.codex import (
    CodexDriver,
    list_codex_models,
    load_codex_defaults,
    resolve_codex_command,
)
from .brains.cursor import CursorDriver, list_cursor_models, resolve_cursor_command
from .brains.gemini import GeminiDriver, GEMINI_DEFAULT_MODEL, list_gemini_models, load_gemini_api_key
from .config import ConfigError, NiraiConfig, save_audio_volume
from .conversation import GroupConversationError, GroupConversationState
from .memory import PrivateMemoryError, PrivateMemoryService, WorldMemoryError, WorldMemoryService
from .protocol import ProtocolError, make_message, parse_message, time_of_day
from .residents.service import ResidentError, ResidentService
from .sessions.chat_store import ChatStore, ChatStoreError
from .sessions.manager import SessionManager


CORE_HOST = "127.0.0.1"
RESIDENT_CHAT_STAND_CLEANUP_TIMEOUT_SEC = 0.2
LOGGER = logging.getLogger("nirai.core.server")


class CoreServer:
    def __init__(
        self,
        config: NiraiConfig,
        *,
        port_override: int | None = None,
        brain_driver: BrainDriver | None = None,
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
        self._request_invocations: dict[str, set[str]] = {}
        self._cancelled_requests: set[str] = set()
        self.audio_volume = config.world.audio_volume
        self.resident_service = ResidentService(config.root, config.residents_enabled)
        self.private_memory = PrivateMemoryService(config.root)
        self.world_memory = WorldMemoryService(config.root)
        self.sessions = SessionManager(ChatStore(config.root / "runtime" / "chat_sessions"))

    @property
    def bound_port(self) -> int | None:
        if self._server is None or not self._server.sockets:
            return None
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        if self._server is not None:
            return
        self._server = await serve(self._handle_connection, self.host, self.port)
        LOGGER.info("server_listening host=%s port=%s", self.host, self.bound_port)

    async def stop(self) -> None:
        if self._server is None:
            return
        LOGGER.info(
            "server_stop_start active_responses=%s active_resident_chats=%s",
            len(self._response_tasks),
            len(self._resident_chat_tasks),
        )
        await self._cancel_all_responses()
        await self._cancel_all_resident_chats()
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

    async def _handle_connection(self, websocket: ServerConnection) -> None:
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
                    if payload.get("role") != "world":
                        continue
                    previous = self._world_connection
                    if previous is not None and previous is not websocket:
                        await previous.close(code=4000, reason="replaced by newer world connection")
                    self._world_connection = websocket
                    LOGGER.info("world_connected")
                    await self._send_hello_ack(websocket, message.get("id"))
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
                        self.resident_service.load(resident_name)
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
                        waiter.cancel()
                    self._action_waiters.pop(action_id, None)
                LOGGER.info("world_disconnected")

    def _brain_provider_list(self) -> list[dict[str, object]]:
        codex_default_model, codex_default_reasoning = load_codex_defaults()
        providers = (
            ("codex", "Codex", "subscription-cli", codex_default_model),
            ("claude-code", "Claude", "subscription-cli", None),
            ("cursor", "Cursor", "subscription-cli", "auto"),
            ("gemini", "Gemini", "api-key", GEMINI_DEFAULT_MODEL),
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
            })
        return result

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

        residents = [
            resident
            for resident in self.resident_service.list_enabled()
            if resident.brain is not None
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
                                "current_residents": list(self.resident_service.enabled_names),
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
                        "current_residents": list(self.resident_service.enabled_names),
                        "public_history": self.sessions.public_history(session_id, limit=20),
                        "current_whisper_history": self.sessions.whisper_history(
                            session_id,
                            resident.name,
                            limit=20,
                        ),
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
                                "current_residents": list(self.resident_service.enabled_names),
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
