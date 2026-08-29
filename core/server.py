from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from websockets.asyncio.server import ServerConnection, serve

from .brains.base import BrainDriver, BrainError, BrainUnavailableError
from .brains.codex import CodexDriver, resolve_codex_command
from .config import ConfigError, NiraiConfig, save_audio_volume
from .memory import PrivateMemoryError, PrivateMemoryService, WorldMemoryError, WorldMemoryService
from .protocol import ProtocolError, make_message, parse_message, time_of_day
from .residents.service import ResidentDefinition, ResidentError, ResidentService
from .sessions.chat_store import ChatStore, ChatStoreError
from .sessions.manager import SessionManager


CORE_HOST = "127.0.0.1"
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
        self._brain_driver = brain_driver
        self._response_tasks: dict[str, asyncio.Task[None]] = {}
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
        LOGGER.info("server_stop_start active_responses=%s", len(self._response_tasks))
        await self._cancel_all_responses()
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
                    if message_type == "brain_provider_list_request":
                        await websocket.send(
                            make_message(
                                "brain_provider_list",
                                {"providers": self._brain_provider_list()},
                                message.get("id"),
                            )
                        )
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
                        resident = self.resident_service.create(name, provider)
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
                        resident = self.resident_service.set_brain(name, provider)
                        LOGGER.info("resident_set_brain_applied name=%s provider=%s", name, provider)
                        await websocket.send(
                            make_message(
                                "resident_settings_updated",
                                {"resident": resident.to_protocol()},
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
                LOGGER.info("world_disconnected")

    def _brain_provider_list(self) -> list[dict[str, object]]:
        codex_available = self._provider_is_available("codex")
        return [
            {
                "name": "codex",
                "display_name": "Codex",
                "available": codex_available,
                "connected": codex_available,
                "configuration_mode": "subscription-cli",
            },
            *[
                {
                    "name": name,
                    "display_name": display_name,
                    "available": False,
                    "connected": False,
                    "configuration_mode": "unavailable",
                }
                for name, display_name in (
                    ("claude-code", "Claude"),
                    ("cursor", "Cursor"),
                    ("gemini", "Gemini"),
                    ("local-llm", "Local LLM"),
                )
            ],
        ]

    def _provider_is_available(self, provider: str) -> bool:
        if provider != "codex":
            return False
        try:
            resolve_codex_command()
        except BrainUnavailableError:
            return False
        return True

    def _get_brain_driver(self, provider: str) -> BrainDriver:
        if self._brain_driver is not None:
            return self._brain_driver
        if provider != "codex":
            raise BrainError(f"Brain provider is not available in M1 yet: {provider}")
        self._brain_driver = CodexDriver(self.config.root)
        return self._brain_driver

    def _m1_talk_resident(self) -> ResidentDefinition | None:
        for resident in self.resident_service.list_enabled():
            if resident.brain is not None:
                return resident
        return None

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
        driver = self._brain_driver
        if driver is not None:
            for invocation_id in invocation_ids:
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
        driver = self._brain_driver
        if driver is not None:
            invocation_ids = {
                invocation_id
                for ids in self._request_invocations.values()
                for invocation_id in ids
            }
            for invocation_id in invocation_ids:
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

        resident = self._m1_talk_resident()
        if resident is None or resident.brain is None:
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
            if request_id in self._cancelled_requests:
                return
            response = await self._get_brain_driver(resident.brain).think(
                invocation_id,
                "talk",
                {"name": resident.name, "persona": self.resident_service.read_persona(resident.name)},
                {"history": self.sessions.public_history(session_id, limit=20)},
            )
            if request_id in self._cancelled_requests:
                LOGGER.info(
                    "brain_result_discarded_cancelled request_id=%s invocation_id=%s",
                    request_id,
                    invocation_id,
                )
                return
            LOGGER.info(
                "brain_success request_id=%s invocation_id=%s elapsed_ms=%d has_say=%s pass=%s",
                request_id,
                invocation_id,
                round((perf_counter() - started_at) * 1000),
                bool(response.say),
                response.passed,
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
                    "resident_say_saved request_id=%s invocation_id=%s session_id=%s",
                    request_id,
                    invocation_id,
                    session_id,
                )
        except (BrainError, ResidentError) as exc:
            if request_id in self._cancelled_requests:
                LOGGER.info(
                    "brain_cancelled request_id=%s invocation_id=%s elapsed_ms=%d",
                    request_id,
                    invocation_id,
                    round((perf_counter() - started_at) * 1000),
                )
            else:
                safe_error = str(exc)[:500].replace("\r", "\\r").replace("\n", "\\n")
                LOGGER.warning(
                    "brain_failed request_id=%s invocation_id=%s elapsed_ms=%d error_type=%s error=%s",
                    request_id,
                    invocation_id,
                    round((perf_counter() - started_at) * 1000),
                    type(exc).__name__,
                    safe_error,
                )
                await websocket.send(
                    make_message(
                        "notice",
                        {"level": "WARN", "text": str(exc)},
                    )
                )
        finally:
            invocation_ids = self._request_invocations.get(request_id)
            if invocation_ids is not None:
                invocation_ids.discard(invocation_id)
                if not invocation_ids:
                    self._request_invocations.pop(request_id, None)
            self._cancelled_requests.discard(request_id)
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
            response = await self._get_brain_driver(resident.brain).think(
                invocation_id,
                "whisper",
                {"name": resident.name, "persona": self.resident_service.read_persona(resident.name)},
                {
                    **private_context,
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
