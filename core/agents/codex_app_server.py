from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from core.brains.base import BrainUnavailableError
from core.brains.codex import load_codex_defaults, resolve_codex_command

from .base import (
    AgentProviderLimitError,
    AgentRunRequest,
    AgentRunResult,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    EmitEvent,
    WaitForMaster,
    classify_provider_limit,
)
from .codex_events import normalize_codex_notification
from .codex_credentials import (
    CodexCredentialsMixin,
    _HOME_REMOVE_RETRY_DELAYS_SEC,
    _CODEX_STALE_RUNTIME_AGE_SEC,
)
from .cursor_policy import CURSOR_WRITABLE_IGNORE_NAMES
from .cursor_workspace import CursorWorkspaceMixin, StagedApplyCancelledAfterCommit
from .safety import AgentSafetyError, AgentWorkspacePolicy


LOGGER = logging.getLogger("nirai.core.agent.codex")
_PROCESS_TREE_STOP_TIMEOUT_SEC = 8.0
_PROCESS_WAIT_STEP_SEC = 2.0
_CLIENT_TASK_FINISH_TIMEOUT_SEC = 1.0
_STDERR_READ_CHUNK_BYTES = 512
_STDERR_LINE_BUFFER_BYTES = 2048
_DIAGNOSTIC_EXCERPT_CHARS = 500


class _RpcError(RuntimeError):
    def __init__(self, error: object) -> None:
        super().__init__(_rpc_error_message(error))
        self.provider_limit = classify_provider_limit(error)


class _CodexCredentialCleanupError(AgentRuntimeUnavailableError):
    """Provider is quiesced, but transient Codex credential material remains."""


ServerRequestHandler = Callable[[object, str, dict[str, Any]], Awaitable[dict[str, Any]]]
NotificationHandler = Callable[[str, dict[str, Any]], Awaitable[None]]


class _JsonLineAppServer:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        server_request_handler: ServerRequestHandler,
        notification_handler: NotificationHandler,
    ) -> None:
        self.process = process
        self.server_request_handler = server_request_handler
        self.notification_handler = notification_handler
        self._next_id = 1
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._request_tasks: set[asyncio.Task[None]] = set()
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[request_id] = future
        payload: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = params
        try:
            await self._send(payload)
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        payload: dict[str, Any] = {"method": method}
        if params is not None:
            payload["params"] = params
        await self._send(payload)

    async def close(self) -> None:
        stopped = False
        try:
            stopped = await asyncio.wait_for(
                _terminate_process_tree(self.process),
                timeout=_PROCESS_TREE_STOP_TIMEOUT_SEC,
            )
        except (OSError, asyncio.TimeoutError):
            LOGGER.warning("codex_app_server_close_stop_failed", exc_info=True)
        finally:
            await self._finish_tasks()
        if not stopped and self.process.returncode is None:
            raise AgentRuntimeUnavailableError("Codex app-server process could not be stopped")

    async def _send(self, payload: dict[str, Any]) -> None:
        stdin = self.process.stdin
        if stdin is None or self.process.returncode is not None:
            raise AgentRuntimeProtocolError("Codex app-server stdin is unavailable")
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            stdin.write(encoded)
            try:
                await stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AgentRuntimeProtocolError("Codex app-server pipe closed") from exc

    async def _read_loop(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            self._fail_pending(AgentRuntimeProtocolError("Codex app-server stdout is unavailable"))
            return
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    LOGGER.warning(
                        "codex_app_server_invalid_json",
                        extra={"line": repr(raw[:500])},
                    )
                    continue
                if not isinstance(message, dict):
                    continue
                method = message.get("method")
                message_id = message.get("id")
                if isinstance(method, str) and message_id is not None:
                    params = message.get("params")
                    task = asyncio.create_task(
                        self._handle_server_request(
                            message_id,
                            method,
                            params if isinstance(params, dict) else {},
                        )
                    )
                    self._request_tasks.add(task)
                    task.add_done_callback(self._request_tasks.discard)
                    continue
                if isinstance(method, str):
                    params = message.get("params")
                    await self.notification_handler(
                        method,
                        params if isinstance(params, dict) else {},
                    )
                    continue
                if isinstance(message_id, int):
                    future = self._pending.get(message_id)
                    if future is None or future.done():
                        continue
                    error = message.get("error")
                    if error is not None:
                        future.set_exception(_RpcError(error))
                        continue
                    result = message.get("result")
                    future.set_result(result if isinstance(result, dict) else {})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive transport boundary
            LOGGER.exception("codex_app_server_reader_failed")
            self._fail_pending(AgentRuntimeProtocolError("Codex app-server reader failed"))
            return
        finally:
            if self.process.returncode is None:
                try:
                    await asyncio.wait_for(
                        self.process.wait(),
                        timeout=_PROCESS_WAIT_STEP_SEC,
                    )
                except asyncio.TimeoutError:
                    pass
            self._fail_pending(
                AgentRuntimeProtocolError(
                    f"Codex app-server exited unexpectedly with code {self.process.returncode}"
                )
            )

    async def _handle_server_request(
        self,
        request_id: object,
        method: str,
        params: dict[str, Any],
    ) -> None:
        try:
            result = await self.server_request_handler(request_id, method, params)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("codex_app_server_server_request_failed", extra={"method": method})
            await self._send({
                "id": request_id,
                "error": {"code": -32000, "message": str(exc) or type(exc).__name__},
            })
            return
        await self._send({"id": request_id, "result": result})

    async def _drain_stderr(self) -> None:
        stderr = self.process.stderr
        if stderr is None:
            return
        line = bytearray()
        discarding = False
        while True:
            raw = await stderr.read(_STDERR_READ_CHUNK_BYTES)
            if not raw:
                if line and not discarding:
                    LOGGER.debug("codex_app_server_stderr: %s", _safe_diagnostic_excerpt(bytes(line)))
                return
            for byte in raw:
                if discarding:
                    if byte == 0x0A:
                        discarding = False
                    continue
                if byte == 0x0A:
                    LOGGER.debug("codex_app_server_stderr: %s", _safe_diagnostic_excerpt(bytes(line)))
                    line.clear()
                    continue
                if len(line) < _STDERR_LINE_BUFFER_BYTES:
                    line.append(byte)
                    continue
                LOGGER.debug("codex_app_server_stderr: %s", _safe_diagnostic_excerpt(bytes(line)))
                line.clear()
                discarding = True

    def _fail_pending(self, exc: Exception) -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(exc)

    async def _finish_tasks(self) -> None:
        tasks = (self._reader_task, self._stderr_task, *self._request_tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_CLIENT_TASK_FINISH_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            LOGGER.warning("codex_app_server_task_cleanup_timeout")


@dataclass
class _ActiveCodexRun:
    client: _JsonLineAppServer
    thread_id: str | None = None
    turn_id: str | None = None
    cancel_requested: bool = False
    provider_success_observed_before_cancel: bool = False


class CodexAppServerAdapter(CursorWorkspaceMixin, CodexCredentialsMixin):
    provider = "codex"
    capabilities = frozenset({
        "approval",
        "question",
        "plan",
        "todo",
        "subagent",
        "file_diff",
        "command_result",
    })

    _APPROVAL_METHODS = {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }
    _QUESTION_METHOD = "item/tool/requestUserInput"
    _DECISIONS = {
        "approve_once": "accept",
        "approve_session": "acceptForSession",
        "reject": "decline",
        "cancel": "cancel",
    }

    def __init__(self, workspace_policy: AgentWorkspacePolicy) -> None:
        self.workspace_policy = workspace_policy
        self.root = workspace_policy.root
        self._active: dict[str, _ActiveCodexRun] = {}
        self._preparing_ids: set[str] = set()
        self._active_lock = asyncio.Lock()
        self._runtime_owned_ids: set[str] = set()
        self._cancel_intent_ids: set[str] = set()
        self._runtime_owned_ids_lock = threading.Lock()

    def _claim_runtime_id(self, agent_session_id: str) -> None:
        with self._runtime_owned_ids_lock:
            self._runtime_owned_ids.add(agent_session_id)

    def _release_runtime_id(self, agent_session_id: str) -> None:
        with self._runtime_owned_ids_lock:
            self._runtime_owned_ids.discard(agent_session_id)
            self._cancel_intent_ids.discard(agent_session_id)

    def _cancel_intent_requested(self, agent_session_id: str) -> bool:
        with self._runtime_owned_ids_lock:
            return agent_session_id in self._cancel_intent_ids

    def clear_cancel_intent(self, agent_session_id: str) -> None:
        with self._runtime_owned_ids_lock:
            self._cancel_intent_ids.discard(agent_session_id)

    def _runtime_owned_snapshot(self) -> set[str]:
        with self._runtime_owned_ids_lock:
            return set(self._runtime_owned_ids)

    async def _prepare_isolated_codex_home_cancellation_safe(
        self,
        agent_session_id: str,
        *,
        conversation_id: str | None,
        preserve_conversation_home: bool,
    ) -> Path:
        prepare_task = asyncio.create_task(
            asyncio.to_thread(
                self._prepare_isolated_codex_home,
                agent_session_id,
                conversation_id=conversation_id,
            ),
            name=f"codex-home-prepare-{agent_session_id}",
        )
        try:
            return await asyncio.shield(prepare_task)
        except asyncio.CancelledError:
            # The filesystem worker keeps running after asyncio cancellation.
            # Reap any late-created credential copy before ownership is released.
            try:
                isolated_home = await prepare_task
            except BaseException:
                isolated_home = None
            if isolated_home is not None:
                if preserve_conversation_home:
                    await asyncio.to_thread(
                        self._remove_conversation_secret_material,
                        isolated_home,
                    )
                else:
                    await asyncio.to_thread(self._remove_isolated_home, isolated_home)
            raise

    async def _spawn_cancellation_safe(
        self,
        command: tuple[str, ...],
        working_dir: Path,
        *,
        env: dict[str, str] | None,
    ) -> asyncio.subprocess.Process:
        spawn_task = asyncio.create_task(
            self._spawn(command, working_dir, env=env),
            name="codex-app-server-spawn",
        )
        try:
            return await asyncio.shield(spawn_task)
        except asyncio.CancelledError as cancel_exc:
            # Process creation can finish after the caller is cancelled. Give it
            # the same finite process-stop window to settle so a late handle can
            # be recovered, but never turn cancellation into an unbounded wait.
            try:
                process = await asyncio.wait_for(
                    asyncio.shield(spawn_task),
                    timeout=_PROCESS_TREE_STOP_TIMEOUT_SEC,
                )
            except asyncio.TimeoutError:
                spawn_task.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.gather(spawn_task, return_exceptions=True),
                        timeout=_CLIENT_TASK_FINISH_TIMEOUT_SEC,
                    )
                except asyncio.TimeoutError:
                    LOGGER.warning("codex_app_server_spawn_task_cleanup_timeout")
                raise cancel_exc
            except BaseException:
                process = None
            if process is not None:
                try:
                    stopped = await asyncio.wait_for(
                        _terminate_process_tree(process),
                        timeout=_PROCESS_TREE_STOP_TIMEOUT_SEC,
                    )
                except (OSError, asyncio.TimeoutError) as exc:
                    raise AgentRuntimeUnavailableError(
                        "Codex app-server spawn cancellation could not stop the process"
                    ) from exc
                if not stopped and process.returncode is None:
                    raise AgentRuntimeUnavailableError(
                        "Codex app-server spawn cancellation left a live process"
                    ) from cancel_exc
            raise

    async def _finalize_run_resources_cancellation_safe(
        self,
        client: _JsonLineAppServer,
        isolated_codex_home: Path,
        *,
        preserve_conversation_home: bool,
    ) -> tuple[bool, _CodexCredentialCleanupError | None]:
        finalize_task = asyncio.create_task(
            self._finalize_run_resources(
                client,
                isolated_codex_home,
                preserve_conversation_home=preserve_conversation_home,
            ),
            name=f"codex-finalize-{isolated_codex_home.name}",
        )
        cancelled_during_cleanup = False
        try:
            await asyncio.shield(finalize_task)
        except asyncio.CancelledError:
            cancelled_during_cleanup = True
            try:
                await finalize_task
            except _CodexCredentialCleanupError as exc:
                return cancelled_during_cleanup, exc
        except _CodexCredentialCleanupError as exc:
            return cancelled_during_cleanup, exc
        return cancelled_during_cleanup, None

    async def fetch_rate_limits(self) -> dict[str, Any]:
        """Read Codex account rate-limit state through the official app-server RPC."""
        usage_id = f"USAGE-{uuid4()}"
        command = self._resolve_command()
        isolated_home: Path | None = None
        client: _JsonLineAppServer | None = None
        self._claim_runtime_id(usage_id)

        async def reject_server_request(
            _request_id: object,
            method: str,
            _params: dict[str, Any],
        ) -> dict[str, Any]:
            raise AgentRuntimeProtocolError(
                f"Codex usage monitor received unexpected server request: {method}"
            )

        async def ignore_notification(_method: str, _params: dict[str, Any]) -> None:
            return None

        try:
            isolated_home = await self._prepare_isolated_codex_home_cancellation_safe(
                usage_id,
                conversation_id=None,
                preserve_conversation_home=False,
            )
            child_env = self._build_child_env(isolated_home)
            process = await self._spawn_cancellation_safe(
                command,
                self.root,
                env=child_env,
            )
            client = _JsonLineAppServer(
                process,
                server_request_handler=reject_server_request,
                notification_handler=ignore_notification,
            )
            await client.request("initialize", {
                "clientInfo": {
                    "name": "nirai",
                    "title": "Nirai Usage Monitor",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": False},
            })
            await client.notify("initialized")
            return await client.request("account/rateLimits/read")
        except _RpcError as exc:
            raise AgentRuntimeProtocolError(str(exc)) from exc
        finally:
            try:
                if client is not None and isolated_home is not None:
                    cancelled, cleanup_error = await self._finalize_run_resources_cancellation_safe(
                        client,
                        isolated_home,
                        preserve_conversation_home=False,
                    )
                    if cleanup_error is not None:
                        raise cleanup_error
                    if cancelled:
                        raise asyncio.CancelledError()
                elif isolated_home is not None:
                    await asyncio.to_thread(self._remove_isolated_home, isolated_home)
            finally:
                self._release_runtime_id(usage_id)

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None | AgentRunResult:
        command = self._resolve_command()
        completion: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        final_messages: list[str] = []
        active: _ActiveCodexRun
        real_working_dir = request.working_dir.resolve()
        provider_working_dir = real_working_dir
        staging_dir: Path | None = None
        staging_baseline: dict[str, tuple[int, str]] | None = None
        staging_ignore_parts = CURSOR_WRITABLE_IGNORE_NAMES

        async def handle_notification(method: str, params: dict[str, Any]) -> None:
            # Codex app-server exposes context compaction as a first-class item.
            # A compacted thread keeps the same Thread identity, but arbitrary
            # user-provided context inside old turns may have been summarized.
            # Tell the Conversation layer so it can refresh Nirai-owned static
            # context on the next turn instead of inventing a new Thread.
            if method == "item/completed":
                item = params.get("item")
                if isinstance(item, dict) and item.get("type") == "contextCompaction":
                    await emit("run_state", {
                        "state": "running",
                        "context_compacted": True,
                    })

            for event_type, payload in normalize_codex_notification(
                method,
                params,
                working_dir=provider_working_dir,
                workspace_policy=self.workspace_policy,
            ):
                if not request.read_only and event_type in {"file_change", "artifact"}:
                    # Writable Codex runs operate only in staging. Do not expose
                    # provisional stage paths as if the real workspace changed;
                    # one frozen aggregate diff is emitted after provider stop.
                    continue
                if not request.read_only and event_type == "command_execution":
                    payload = {
                        **payload,
                        "cwd": str(real_working_dir),
                        "execution_scope": "codex_staging",
                    }
                if event_type == "assistant_message":
                    text = payload.get("text")
                    phase = payload.get("phase")
                    message_phase = payload.get("message_phase")
                    if (
                        isinstance(text, str)
                        and text
                        and phase == "completed"
                        and message_phase in {None, "final_answer"}
                    ):
                        final_messages.append(text)
                await emit(event_type, payload)

            if method == "turn/started":
                turn = params.get("turn")
                if isinstance(turn, dict) and isinstance(turn.get("id"), str):
                    active.turn_id = turn["id"]
                await emit("run_state", {"state": "running"})
            elif method == "turn/completed":
                turn = params.get("turn")
                if isinstance(turn, dict) and not completion.done():
                    # Preserve the ordering fact itself, not merely the later
                    # coexistence of `cancel_requested` and provider success.
                    # A completed notification observed only after Nirai's
                    # cancel intent is late evidence and must not become the
                    # explicit committed-work override used by Manager.
                    if turn.get("status") == "completed" and not active.cancel_requested:
                        active.provider_success_observed_before_cancel = True
                    completion.set_result(turn)

        async def handle_server_request(
            provider_request_id: object,
            method: str,
            params: dict[str, Any],
        ) -> dict[str, Any]:
            request_key = _provider_request_key(provider_request_id)
            if method in self._APPROVAL_METHODS:
                if request.read_only:
                    await emit("status_message", {
                        "kind": "codex_read_only_approval_declined",
                        "text": "Codex operation declined by read-only Conversation policy",
                    })
                    return {"decision": "decline"}
                requires_master = (
                    _codex_staged_command_requires_master(params)
                    if method == "item/commandExecution/requestApproval"
                    else False
                )
                if method == "item/fileChange/requestApproval":
                    try:
                        _validate_file_change_approval(
                            params,
                            workspace_policy=self.workspace_policy,
                            working_dir=provider_working_dir,
                        )
                        # Every writable Codex file change lands in staging.
                        # Aggregate destructive impact is judged once from the
                        # frozen end-of-turn diff, never from provider-sized chunks.
                        requires_master = False
                    except AgentSafetyError as exc:
                        await emit("error", {
                            "message": str(exc),
                            "code": "file_change_approval_rejected",
                            "recoverable": False,
                        })
                        return {"decision": "decline"}

                if not requires_master:
                    await emit("status_message", {
                        "kind": "codex_operation_auto_approved",
                        "text": "Codex workspace operation passed Nirai safety checks and was approved automatically",
                    })
                    return {"decision": "accept"}

                approval_payload = _common_approval_payload(request_key, method, params)
                approval_payload["title"] = "Codex commit / push operation requires approval"
                await emit("approval_request", approval_payload)
                answer = await wait_for_master(request_key, "approval", approval_payload)
                raw_decision = answer.get("decision")
                decision = self._DECISIONS.get(raw_decision) if isinstance(raw_decision, str) else None
                if decision is None:
                    decision = "decline"
                return {"decision": decision}

            if method == self._QUESTION_METHOD:
                if request.read_only:
                    await emit("status_message", {
                        "kind": "codex_read_only_question_skipped",
                        "text": "Codex tool question skipped by read-only Conversation policy",
                    })
                    return {"answers": {}}
                question_payload = _common_question_payload(request_key, params)
                await emit("question_request", question_payload)
                answer = await wait_for_master(request_key, "question", question_payload)
                return _codex_question_response(answer)

            LOGGER.warning("codex_unsupported_server_request method=%s", method)
            await emit("error", {
                "message": "Codex requested an operation Nirai does not support yet.",
                "code": "unsupported_provider_request",
                "recoverable": False,
            })
            raise AgentRuntimeProtocolError(f"Unsupported Codex server request: {method}")

        preserve_conversation_home = request.read_only and request.conversation_id is not None
        provider_work_succeeded = False
        # Own the Agent home identity before stale cleanup begins. `_active` is
        # populated only after process creation, so it cannot protect a sibling
        # that is still preparing its isolated credentials.
        self._claim_runtime_id(request.agent_session_id)
        try:
            if not request.read_only:
                if request.purpose == "integrated_audit":
                    self.workspace_policy.resolve_integrated_audit_working_dir(
                        str(real_working_dir),
                        task_id=request.task_id,
                    )
                    staging_ignore_parts = self._writable_staging_ignore_parts(
                        real_working_dir,
                        integrated_audit=True,
                    )
                else:
                    self.workspace_policy.resolve_working_dir(
                        str(real_working_dir),
                        task_id=request.task_id,
                    )
                    staging_ignore_parts = self._writable_staging_ignore_parts(real_working_dir)
                staging_dir, staging_baseline = await self._prepare_staging_workspace_cancellation_safe(
                    request.agent_session_id,
                    real_working_dir,
                    ignore_parts=staging_ignore_parts,
                    stable_key=None,
                    staging_root=self._staging_root_for(real_working_dir, read_only=False),
                )
                provider_working_dir = staging_dir

            isolated_codex_home = await self._prepare_isolated_codex_home_cancellation_safe(
                request.agent_session_id,
                conversation_id=request.conversation_id if preserve_conversation_home else None,
                preserve_conversation_home=preserve_conversation_home,
            )
            child_env = self._build_child_env(isolated_codex_home)
            if staging_dir is not None:
                _add_staging_dependency_env(child_env, real_working_dir)
                # Staging intentionally omits .git. Prevent Git commands from
                # walking up to the real Nirai repository outside staging.
                child_env["GIT_CEILING_DIRECTORIES"] = str(staging_dir)
            try:
                process = await self._spawn_cancellation_safe(
                    command,
                    provider_working_dir,
                    env=child_env,
                )
            except BaseException:
                # Spawn failure/cancellation is still an owned-resource exit
                # path. `_spawn_cancellation_safe` reaps any late process first;
                # remove credentials before the runtime claim becomes available.
                if preserve_conversation_home:
                    await asyncio.to_thread(
                        self._remove_conversation_secret_material,
                        isolated_codex_home,
                    )
                else:
                    await asyncio.to_thread(self._remove_isolated_home, isolated_codex_home)
                raise
        except BaseException:
            if staging_dir is not None:
                try:
                    await asyncio.to_thread(self._cleanup_staging_workspace, staging_dir)
                except Exception:
                    LOGGER.warning(
                        "codex_staging_prepare_cleanup_failed agent_session_id=%s",
                        request.agent_session_id,
                        exc_info=True,
                    )
            self._preparing_ids.discard(request.agent_session_id)
            self._release_runtime_id(request.agent_session_id)
            raise
        client = _JsonLineAppServer(
            process,
            server_request_handler=handle_server_request,
            notification_handler=handle_notification,
        )
        active = _ActiveCodexRun(
            client=client,
            cancel_requested=self._cancel_intent_requested(request.agent_session_id),
        )
        active_registered = False

        # From this point onward the process handle, credential Home, and runtime
        # ownership are all covered by one finally, including cancellation while
        # waiting to register the active run.
        try:
            async with self._active_lock:
                # Cancel intent can arrive while this run is waiting to acquire
                # `_active_lock`, after the local Active object was constructed.
                # Re-read at registration so that last pre-registration gap is
                # ordered correctly; after insertion, mark_cancel_intent updates
                # the Active object directly with no intervening await.
                if self._cancel_intent_requested(request.agent_session_id):
                    active.cancel_requested = True
                self._active[request.agent_session_id] = active
                active_registered = True
            await client.request("initialize", {
                "clientInfo": {
                    "name": "nirai",
                    "title": "Nirai Agent Runtime",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": False},
            })
            await client.notify("initialized")

            if request.read_only:
                boundary_instruction = (
                    "Nirai read-only Conversation boundary: inspect files only inside the current working directory. "
                    "Do not create, modify, move, or delete files. Do not access user-home files, sibling repositories, "
                    "credentials, environment secrets, global skills, or configuration outside the working directory. "
                    "Network access is disabled. Do not request approval or tool input; if clarification is useful, "
                    "state it naturally in the final answer."
                )
            else:
                boundary_instruction = (
                    "Nirai Agent Runtime boundary: this turn runs only in an isolated staging copy of the Task workspace. "
                    "Do not read user-home files, sibling repositories, credentials, environment secrets, global skills, "
                    "or configuration outside the staging workspace. Normal workspace tools continue automatically. "
                    "Commit and push require Master approval. Destructive staging edits are allowed, but Nirai reviews the "
                    "complete frozen diff and requires Master approval before any broad deletion reaches the real workspace. "
                    "Network access is disabled."
                )
            default_model, default_reasoning_effort = load_codex_defaults()
            effective_model = request.model or default_model
            effective_reasoning_effort = request.reasoning_effort or default_reasoning_effort
            thread_params: dict[str, Any] = {
                "cwd": str(provider_working_dir),
                "approvalPolicy": "untrusted",
                "approvalsReviewer": "user",
                "sandbox": "read-only" if request.read_only else "workspace-write",
                "developerInstructions": boundary_instruction,
            }
            if effective_model:
                thread_params["model"] = effective_model
            if request.provider_session_id is not None:
                thread_result = await client.request(
                    "thread/resume",
                    {"threadId": request.provider_session_id, **thread_params},
                )
            else:
                thread_result = await client.request("thread/start", thread_params)
            thread = thread_result.get("thread")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                raise AgentRuntimeProtocolError("Codex thread/start did not return thread.id")
            active.thread_id = thread["id"]
            if request.provider_session_id is not None and active.thread_id != request.provider_session_id:
                raise AgentRuntimeProtocolError("Codex thread/resume returned a different thread id")
            await emit("run_state", {
                "state": "running",
                "provider_session_id": active.thread_id,
            })
            await emit("status_message", {
                "message": "Codex thread resumed" if request.provider_session_id is not None else "Codex thread started"
            })

            turn_params: dict[str, Any] = {
                "threadId": active.thread_id,
                "input": [{"type": "text", "text": request.prompt}],
                "cwd": str(provider_working_dir),
                "approvalPolicy": "untrusted",
                "approvalsReviewer": "user",
                "sandboxPolicy": (
                    {"type": "readOnly", "networkAccess": False}
                    if request.read_only
                    else {
                        "type": "workspaceWrite",
                        "writableRoots": [str(provider_working_dir)],
                        "networkAccess": False,
                    }
                ),
            }
            if effective_model:
                turn_params["model"] = effective_model
            if effective_reasoning_effort:
                turn_params["effort"] = effective_reasoning_effort
            turn_result = await client.request("turn/start", turn_params)
            turn = turn_result.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                raise AgentRuntimeProtocolError("Codex turn/start did not return turn.id")
            active.turn_id = turn["id"]
            await emit("run_state", {"state": "running"})

            completed_turn = await completion
            status = completed_turn.get("status")
            if status == "failed":
                error = completed_turn.get("error")
                provider_limit = classify_provider_limit(error)
                if provider_limit is not None:
                    raise provider_limit
                message = _turn_error_message(error)
                raise AgentRuntimeProtocolError(message)
            if status == "interrupted":
                await emit("run_state", {"state": "cancelled"})
                return final_messages[-1] if final_messages else None
            if status != "completed":
                raise AgentRuntimeProtocolError(f"Codex turn ended in unexpected state: {status!r}")

            if staging_dir is not None and staging_baseline is not None:
                # No provider process may remain capable of changing staging while
                # Nirai freezes and reviews the aggregate end-of-turn diff.
                if not await _terminate_process_tree(process):
                    raise AgentRuntimeUnavailableError(
                        "Codex app-server could not be stopped before staged review"
                    )
                try:
                    await self._review_and_apply_staged_changes(
                        request,
                        staging_dir=staging_dir,
                        review_dir=isolated_codex_home / ".nirai-staged-review",
                        baseline=staging_baseline,
                        ignore_parts=staging_ignore_parts,
                        emit=emit,
                        wait_for_master=wait_for_master,
                        provider_label="Codex",
                        operation_prefix="codex-stage-apply",
                        auto_apply_kind="codex_stage_auto_apply",
                    )
                except StagedApplyCancelledAfterCommit:
                    provider_work_succeeded = True
                    raise

            provider_work_succeeded = True
            return final_messages[-1] if final_messages else None
        except (AgentProviderLimitError, _RpcError) as exc:
            provider_limit = exc if isinstance(exc, AgentProviderLimitError) else exc.provider_limit
            if provider_limit is None:
                raise AgentRuntimeProtocolError(str(exc)) from exc
            if not await _terminate_process_tree(process):
                raise AgentRuntimeUnavailableError(
                    "Codex app-server could not be stopped after provider limit"
                ) from exc
            partial_path = None
            if staging_dir is not None and staging_baseline is not None:
                partial_path = await self._preserve_partial_staged_changes_cancellation_safe(
                    request,
                    working_dir=real_working_dir,
                    staging_dir=staging_dir,
                    baseline=staging_baseline,
                    ignore_parts=staging_ignore_parts,
                    reason=provider_limit.code,
                )
            raise provider_limit.with_partial_work(partial_path) from exc
        finally:
            cleanup_cancelled = False
            cleanup_error: _CodexCredentialCleanupError | None = None
            try:
                cleanup_cancelled, cleanup_error = (
                    await self._finalize_run_resources_cancellation_safe(
                        client,
                        isolated_codex_home,
                        preserve_conversation_home=preserve_conversation_home,
                    )
                )
                if cleanup_error is not None:
                    if not provider_work_succeeded:
                        raise cleanup_error
                    # The Codex turn is already complete and the provider process
                    # is quiesced. Keep that completed result non-rerunnable while
                    # surfacing credential residue as a separate safety error.
                    try:
                        await emit("error", {
                            "code": "provider_cleanup_failed",
                            "message": str(cleanup_error),
                            "recoverable": False,
                        })
                    except Exception:
                        LOGGER.error(
                            "codex_cleanup_error_event_failed agent_session_id=%s",
                            request.agent_session_id,
                            exc_info=True,
                        )
            finally:
                if active_registered:
                    async with self._active_lock:
                        self._active.pop(request.agent_session_id, None)
                self._preparing_ids.discard(request.agent_session_id)
                if staging_dir is not None:
                    try:
                        await asyncio.to_thread(self._cleanup_staging_workspace, staging_dir)
                    except Exception as exc:
                        LOGGER.warning(
                            "codex_staging_cleanup_failed agent_session_id=%s",
                            request.agent_session_id,
                            exc_info=True,
                        )
                        if provider_work_succeeded:
                            try:
                                await emit("error", {
                                    "code": "provider_cleanup_failed",
                                    "message": f"Codex staging cleanup failed: {exc}",
                                    "recoverable": False,
                                })
                            except Exception:
                                LOGGER.error(
                                    "codex_staging_cleanup_error_event_failed agent_session_id=%s",
                                    request.agent_session_id,
                                    exc_info=True,
                                )
                self._release_runtime_id(request.agent_session_id)
            if (
                provider_work_succeeded
                and active.provider_success_observed_before_cancel
                and (cleanup_cancelled or active.cancel_requested)
            ):
                return AgentRunResult(
                    summary=final_messages[-1] if final_messages else None,
                    work_committed=True,
                )
            if cleanup_cancelled:
                raise asyncio.CancelledError()

    async def _finalize_run_resources(
        self,
        client: _JsonLineAppServer,
        isolated_codex_home: Path,
        *,
        preserve_conversation_home: bool = False,
    ) -> None:
        close_error: BaseException | None = None
        try:
            await client.close()
        except BaseException as exc:
            # Cancellation and OS failures must not skip synchronous credential
            # cleanup. Re-raise cancellation only after the Home is gone.
            close_error = exc
            LOGGER.warning("codex_app_server_close_failed", exc_info=True)

        cleanup_error: Exception | None = None
        try:
            if preserve_conversation_home:
                await asyncio.to_thread(
                    self._remove_conversation_secret_material,
                    isolated_codex_home,
                )
            else:
                await asyncio.to_thread(self._remove_isolated_home, isolated_codex_home)
        except Exception as exc:
            cleanup_error = exc
            LOGGER.error(
                "codex_agent_home_cleanup_failed home=%s",
                isolated_codex_home.name,
                exc_info=True,
            )

        if close_error is not None:
            # A live or incompletely stopped provider is never a post-success
            # cleanup warning: it can still hold/write workspace state.
            if isinstance(close_error, asyncio.CancelledError):
                raise close_error
            raise AgentRuntimeUnavailableError("Codex app-server shutdown failed") from close_error
        if cleanup_error is not None:
            raise _CodexCredentialCleanupError(
                f"Codex Agent credential home cleanup failed: {isolated_codex_home.name}"
            ) from cleanup_error

    def mark_cancel_intent(self, agent_session_id: str) -> bool:
        # Keep the intent even before `_active` registration. Manager owns the
        # cancellation ordering boundary and may enter cancelling while Codex is
        # still preparing/spawning. The run consumes this bit when active state is
        # created, so a later provider completion cannot appear pre-cancel.
        with self._runtime_owned_ids_lock:
            self._cancel_intent_ids.add(agent_session_id)
        active = self._active.get(agent_session_id)
        if active is not None:
            active.cancel_requested = True
        return True

    async def cancel(self, agent_session_id: str) -> bool:
        async with self._active_lock:
            active = self._active.get(agent_session_id)
            if active is None or active.thread_id is None or active.turn_id is None:
                return False
            # Keep direct Adapter cancellation correct too; Manager may already
            # have marked this intent synchronously before reaching formal RPC.
            active.cancel_requested = True
            client = active.client
            thread_id = active.thread_id
            turn_id = active.turn_id
        try:
            await client.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        except (AgentRuntimeProtocolError, _RpcError):
            LOGGER.warning("codex_turn_interrupt_failed", exc_info=True)
            return False
        return True

    def _resolve_command(self) -> tuple[str, ...]:
        try:
            return (*resolve_codex_command(), "app-server", "--stdio")
        except BrainUnavailableError as exc:
            raise AgentRuntimeUnavailableError(str(exc)) from exc

    @staticmethod
    async def _spawn(
        command: tuple[str, ...],
        working_dir: Path,
        *,
        env: dict[str, str] | None = None,
    ) -> asyncio.subprocess.Process:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            return await asyncio.create_subprocess_exec(
                *command,
                cwd=str(working_dir),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=creationflags,
            )
        except (OSError, ValueError) as exc:
            raise AgentRuntimeUnavailableError("Codex app-server could not be started") from exc


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> bool:
    if process.returncode is not None:
        return True

    if os.name == "nt" and process.pid:
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await asyncio.wait_for(killer.wait(), timeout=3.0)
            try:
                await asyncio.wait_for(process.wait(), timeout=_PROCESS_WAIT_STEP_SEC)
            except asyncio.TimeoutError:
                pass
        except (OSError, asyncio.TimeoutError):
            LOGGER.warning("codex_process_tree_stop_failed pid=%s", process.pid, exc_info=True)

    if process.returncode is None:
        try:
            process.terminate()
        except (ProcessLookupError, OSError):
            LOGGER.warning("codex_process_terminate_failed pid=%s", process.pid, exc_info=True)
        else:
            try:
                await asyncio.wait_for(process.wait(), timeout=_PROCESS_WAIT_STEP_SEC)
            except asyncio.TimeoutError:
                pass

    if process.returncode is None:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            LOGGER.warning("codex_process_kill_failed pid=%s", process.pid, exc_info=True)
        else:
            try:
                await asyncio.wait_for(process.wait(), timeout=_PROCESS_WAIT_STEP_SEC)
            except asyncio.TimeoutError:
                LOGGER.warning("codex_process_wait_timeout pid=%s", process.pid)

    if process.returncode is None:
        LOGGER.error("codex_process_stop_incomplete pid=%s", process.pid)
        return False
    return True


def _safe_diagnostic_excerpt(raw: bytes | str) -> str:
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    return text.replace("\r", "\\r").replace("\n", "\\n")[:_DIAGNOSTIC_EXCERPT_CHARS]


def _validate_file_change_approval(
    params: dict[str, Any],
    *,
    workspace_policy: AgentWorkspacePolicy,
    working_dir: Path,
) -> tuple[str, str | None]:
    item_id = params.get("itemId")
    if not isinstance(item_id, str) or not item_id:
        raise AgentSafetyError("Codex File Change approval was rejected because itemId was missing.")
    grant_root = params.get("grantRoot")
    if grant_root is None:
        return item_id, None
    if not isinstance(grant_root, str) or not grant_root.strip():
        raise AgentSafetyError("Codex File Change approval was rejected because grantRoot was invalid.")
    workspace_policy.assert_write_path(Path(grant_root), working_dir=working_dir)
    return item_id, grant_root


def _codex_staged_command_requires_master(params: dict[str, Any]) -> bool:
    """Keep only irreversible VCS publication behind a pre-execution gate.

    Writable Codex commands execute inside an isolated staging workspace. File
    effects, including arbitrary-script deletion, are therefore judged from the
    frozen aggregate diff after the provider has stopped. Commit/push remain a
    direct Master boundary because they can affect repository history or a
    remote outside that file-diff transaction.
    """
    command = params.get("command")
    if isinstance(command, list):
        text = " ".join(value for value in command if isinstance(value, str))
    elif isinstance(command, str):
        text = command
    else:
        return True
    folded = " ".join(text.casefold().split())
    if not folded:
        return True
    git_prefix = r"\bgit(?:\.exe)?\s+(?:(?:-c\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--(?:git-dir|work-tree)(?:=\S+|\s+\S+)|--no-pager)\s+)*"
    folded = re.sub(git_prefix, "git ", folded)
    return re.search(r"\bgit\s+(?:commit|push)\b", folded) is not None


def _add_staging_dependency_env(env: dict[str, str], real_working_dir: Path) -> None:
    """Expose installed dependencies read-only while source writes stay staged.

    Dependency trees are intentionally excluded from staging for cost and
    stability. Codex's workspace sandbox still grants write authority only to
    the staging root; these environment hints merely let test/build tools reuse
    already-installed Node/Python executables from the real project.
    """
    path_prefixes: list[str] = []
    node_modules = real_working_dir / "node_modules"
    if node_modules.is_dir():
        node_bin = node_modules / ".bin"
        if node_bin.is_dir():
            path_prefixes.append(str(node_bin))
        existing_node_path = env.get("NODE_PATH", "")
        env["NODE_PATH"] = os.pathsep.join(
            value for value in (str(node_modules), existing_node_path) if value
        )

    for name in (".venv", "venv"):
        venv_root = real_working_dir / name
        scripts = venv_root / ("Scripts" if os.name == "nt" else "bin")
        if scripts.is_dir():
            path_prefixes.append(str(scripts))
            env.setdefault("VIRTUAL_ENV", str(venv_root))
            break

    if path_prefixes:
        env["PATH"] = os.pathsep.join((*path_prefixes, env.get("PATH", "")))


def _codex_approval_requires_master(method: str, params: dict[str, Any]) -> bool:
    """Compatibility wrapper for the staged Codex approval boundary."""
    if method == "item/fileChange/requestApproval":
        return False
    if method == "item/commandExecution/requestApproval":
        return _codex_staged_command_requires_master(params)
    return True


def _common_approval_payload(
    request_id: str,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    kind = "command" if "commandExecution" in method else "file_change"
    command = params.get("command")
    cwd = params.get("cwd")
    reason = params.get("reason")
    item_id = params.get("itemId")
    grant_root = params.get("grantRoot")
    payload: dict[str, Any] = {
        "request_id": request_id,
        "kind": kind,
        "title": "Command execution requires approval" if kind == "command" else "File changes require approval",
        "options": ["approve_once", "approve_session", "reject", "cancel"],
    }
    if isinstance(item_id, str) and item_id:
        payload["operation_id"] = item_id
    if isinstance(grant_root, str) and grant_root:
        payload["grant_root"] = grant_root
    if isinstance(command, str) and command:
        payload["command"] = command
        payload["description"] = command
    elif isinstance(command, list):
        cleaned_command = [value for value in command if isinstance(value, str)]
        if cleaned_command:
            joined = " ".join(cleaned_command)
            payload["command"] = joined
            payload["description"] = joined
    if isinstance(cwd, str) and cwd:
        payload["cwd"] = cwd
    if isinstance(reason, str) and reason:
        payload["reason"] = reason
        payload.setdefault("description", reason)
    payload.setdefault("description", "Review the requested operation before continuing.")
    return payload


def _common_question_payload(request_id: str, params: dict[str, Any]) -> dict[str, Any]:
    questions: list[dict[str, Any]] = []
    raw_questions = params.get("questions")
    if isinstance(raw_questions, list):
        for value in raw_questions:
            if not isinstance(value, dict):
                continue
            question_id = value.get("id")
            text = value.get("question")
            if not isinstance(question_id, str) or not question_id:
                continue
            if not isinstance(text, str) or not text:
                continue
            normalized: dict[str, Any] = {"id": question_id, "question": text}
            header = value.get("header")
            if isinstance(header, str) and header:
                normalized["header"] = header
            if value.get("isSecret") is True:
                normalized["is_secret"] = True
            options = _common_question_options(value.get("options"))
            if options:
                normalized["options"] = options
            questions.append(normalized)
    return {
        "request_id": request_id,
        "title": "Agent question",
        "questions": questions,
    }


def _common_question_options(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    options: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str) and item:
            options.append({"label": item})
            continue
        if not isinstance(item, dict):
            continue
        label = item.get("label")
        if not isinstance(label, str) or not label:
            continue
        normalized = {"label": label}
        description = item.get("description")
        if isinstance(description, str) and description:
            normalized["description"] = description
        options.append(normalized)
    return options


def _provider_request_key(request_id: object) -> str:
    if isinstance(request_id, (str, int)):
        return str(request_id)
    return json.dumps(request_id, ensure_ascii=False, sort_keys=True, default=str)


def _codex_question_response(answer: dict[str, Any]) -> dict[str, Any]:
    raw_answers = answer.get("answers")
    if not isinstance(raw_answers, dict):
        return {"answers": {}}
    result: dict[str, dict[str, list[str]]] = {}
    for question_id, values in raw_answers.items():
        if not isinstance(question_id, str):
            continue
        if isinstance(values, str):
            cleaned = [values]
        elif isinstance(values, list):
            cleaned = [value for value in values if isinstance(value, str)]
        else:
            continue
        result[question_id] = {"answers": cleaned}
    return {"answers": result}


def _rpc_error_message(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        code = error.get("code")
        if isinstance(message, str):
            return f"Codex RPC error {code}: {message}" if code is not None else message
    return f"Codex RPC error: {error!r}"


def _turn_error_message(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        details = error.get("additionalDetails")
        if isinstance(message, str):
            if isinstance(details, str) and details:
                return f"{message}: {details}"
            return message
    return "Codex turn failed"
