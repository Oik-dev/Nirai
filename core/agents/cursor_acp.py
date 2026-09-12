from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..brains.base import BrainUnavailableError
from ..brains.cursor import _extract_cli_error, _is_unavailable_error, resolve_cursor_command
from ..brains.process_manager import ProcessManager
from .base import (
    AgentProviderLimitError,
    AgentRunRequest,
    AgentRunResult,
    EmitEvent,
    AgentRuntimeError,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    WaitForMaster,
    classify_provider_limit,
)
from .cursor_events import (
    cursor_message_chunk_text,
    cursor_permission_paths,
    normalize_cursor_image,
    normalize_cursor_session_update,
    normalize_cursor_task,
    normalize_cursor_todos,
    validate_cursor_tool_paths,
)
from .safety import AgentSafetyError, AgentWorkspacePolicy

from .cursor_workspace import (
    CursorWorkspaceMixin,
    StagedApplyCancelledAfterCommit,
    _cursor_review_manifest,
    _cursor_file_diff,
    _read_cursor_diff_text,
)
from .cursor_credentials import CursorCredentialsMixin, _cursor_auth_state_source, _path_is_within, _cursor_permission_path
from .cursor_protocol import (
    _cursor_requires_exact_cli_model,
    _is_cursor_review_request,
    _normalize_cursor_review_summary,
    _contains_cursor_login,
    _config_by_category,
    _select_entries,
    _resolve_select_value,
    _cursor_cli_ids_for_acp_option,
    _cursor_model_params,
    _provider_option_id,
    _provider_option_kind,
    _permission_option_by_kind,
    _common_permission_options,
    _permission_option_for_decision,
    _permission_reject_option,
    _permission_reject_result,
    _common_permission_kind,
    _is_external_tool,
    _bounded_text,
)
from .cursor_policy import (
    ACP_REQUEST_TIMEOUT_SEC,
    ACP_STOP_STEP_TIMEOUT_SEC,
    CURSOR_HOME_CLEANUP_RETRIES,
    CURSOR_STAGE_CLEANUP_RETRIES,
    CURSOR_STALE_RUNTIME_AGE_SEC,
    CURSOR_STAGE_FILE_LIMIT,
    CURSOR_STAGE_BYTE_LIMIT,
    CURSOR_DIFF_TEXT_FILE_LIMIT,
    CURSOR_EXACT_CLI_TIMEOUT_SEC,
    CURSOR_EXTERNAL_TOOL_KINDS,
    CURSOR_WRITABLE_IGNORE_NAMES,
    CURSOR_READ_ONLY_IGNORE_NAMES,
    CURSOR_NIRAI_REVIEW_IGNORE_NAMES,
    _ALLOWED_ENV_NAMES,
)


LOGGER = logging.getLogger("nirai.core.agent.cursor_acp")


@dataclass
class _ActiveCursorSession:
    client: "_CursorAcpClient"
    provider_session_id: str | None = None


class _CursorAcpClient:
    def __init__(
        self,
        process: asyncio.subprocess.Process,
        *,
        request_handler: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
        notification_handler: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.process = process
        self._request_handler = request_handler
        self._notification_handler = notification_handler
        self._next_request_id = 1
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._write_lock = asyncio.Lock()
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"cursor-acp-read-{process.pid}")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name=f"cursor-acp-stderr-{process.pid}")

    async def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout_sec: float = ACP_REQUEST_TIMEOUT_SEC,
    ) -> dict[str, Any]:
        request_id = str(self._next_request_id)
        self._next_request_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send({"jsonrpc": "2.0", "id": int(request_id), "method": method, "params": params})
        try:
            message = await asyncio.wait_for(future, timeout=timeout_sec)
        except asyncio.TimeoutError as exc:
            raise AgentRuntimeProtocolError(f"Cursor ACP request timed out: {method}") from exc
        finally:
            self._pending.pop(request_id, None)
        error = message.get("error")
        if error is not None:
            provider_limit = classify_provider_limit(error)
            if provider_limit is not None:
                raise provider_limit
            raise AgentRuntimeProtocolError(
                f"Cursor ACP {method} failed: {_bounded_text(error, 1000)}"
            )
        result = message.get("result")
        if result is None:
            return {}
        if not isinstance(result, dict):
            raise AgentRuntimeProtocolError(f"Cursor ACP {method} returned a non-object result")
        return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": params})

    async def close(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
                await asyncio.wait_for(
                    self.process.stdin.wait_closed(),
                    timeout=ACP_STOP_STEP_TIMEOUT_SEC,
                )
            except (asyncio.TimeoutError, BrokenPipeError, ConnectionResetError, RuntimeError):
                pass
        tasks = (self._reader_task, self._stderr_task)
        for task in tasks:
            if task is not asyncio.current_task() and not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=ACP_STOP_STEP_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            LOGGER.warning("cursor_acp_client_task_cleanup_timeout pid=%s", self.process.pid)

    async def _send(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise AgentRuntimeProtocolError("Cursor ACP stdin is unavailable")
        encoded = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._write_lock:
            try:
                self.process.stdin.write(encoded)
                await self.process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError) as exc:
                raise AgentRuntimeProtocolError("Cursor ACP process disconnected") from exc

    async def _respond(self, request_id: object, result: dict[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def _read_loop(self) -> None:
        stdout = self.process.stdout
        if stdout is None:
            self._fail_pending("Cursor ACP stdout is unavailable")
            return
        try:
            while True:
                raw = await stdout.readline()
                if not raw:
                    break
                if len(raw) > 2_000_000:
                    raise AgentRuntimeProtocolError("Cursor ACP message exceeded the safety limit")
                try:
                    message = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AgentRuntimeProtocolError("Cursor ACP emitted invalid JSON") from exc
                if not isinstance(message, dict):
                    continue
                message_id = message.get("id")
                method = message.get("method")
                if message_id is not None and isinstance(method, str):
                    try:
                        result = await self._request_handler(message)
                    except Exception as exc:
                        LOGGER.warning(
                            "cursor_acp_client_request_failed method=%s error_type=%s error=%s",
                            method,
                            type(exc).__name__,
                            _bounded_text(exc, 500),
                        )
                        await self._send({
                            "jsonrpc": "2.0",
                            "id": message_id,
                            "error": {"code": -32603, "message": "Nirai could not handle the Cursor ACP request"},
                        })
                    else:
                        await self._respond(message_id, result)
                    continue
                if isinstance(method, str):
                    try:
                        await self._notification_handler(message)
                    except Exception:
                        LOGGER.warning(
                            "cursor_acp_notification_failed method=%s",
                            method,
                            exc_info=True,
                        )
                    continue
                if message_id is not None:
                    future = self._pending.get(str(message_id))
                    if future is not None and not future.done():
                        future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning("cursor_acp_reader_failed error=%s", _bounded_text(exc, 500), exc_info=True)
            self._fail_pending(str(exc))
        else:
            if self.process.returncode not in {None, 0}:
                self._fail_pending(f"Cursor ACP exited with code {self.process.returncode}")
            else:
                self._fail_pending("Cursor ACP stdout closed")

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(AgentRuntimeProtocolError(message))

    async def _drain_stderr(self) -> None:
        stderr = self.process.stderr
        if stderr is None:
            return
        try:
            while True:
                chunk = await stderr.read(512)
                if not chunk:
                    return
                LOGGER.debug(
                    "cursor_acp_stderr pid=%s text=%s",
                    self.process.pid,
                    chunk.decode("utf-8", errors="replace")[:500].replace("\r", "\\r").replace("\n", "\\n"),
                )
        except asyncio.CancelledError:
            raise


class CursorAcpAdapter(CursorWorkspaceMixin, CursorCredentialsMixin):
    provider = "cursor"
    # Cursor image/artifact notifications are intentionally suppressed while
    # the provider works in an isolated staging workspace. Do not advertise an
    # artifact capability until Nirai has a safe artifact export/review path.
    capabilities = frozenset({
        "approval",
        "question",
        "plan",
        "todo",
        "subagent",
        "file_diff",
        "command_result",
    })

    def __init__(self, workspace_policy: AgentWorkspacePolicy) -> None:
        self.workspace_policy = workspace_policy
        self.root = workspace_policy.root
        self._active: dict[str, _ActiveCursorSession] = {}
        self._active_lock = asyncio.Lock()
        self._preparing_ids: set[str] = set()
        self._cli_process_manager = ProcessManager()
        self._cli_active_ids: set[str] = set()
        self._cancel_intent_ids: set[str] = set()
        self._runtime_owned_ids: set[str] = set()
        self._runtime_owned_ids_lock = threading.Lock()

    def _claim_runtime_id(self, agent_session_id: str) -> None:
        with self._runtime_owned_ids_lock:
            self._runtime_owned_ids.add(agent_session_id)

    def _release_runtime_id(self, agent_session_id: str) -> None:
        with self._runtime_owned_ids_lock:
            self._runtime_owned_ids.discard(agent_session_id)

    def _runtime_owned_snapshot(self) -> set[str]:
        with self._runtime_owned_ids_lock:
            return set(self._runtime_owned_ids)

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None | AgentRunResult:
        if _cursor_requires_exact_cli_model(request.model):
            return await self._run_exact_cli(
                request,
                emit=emit,
                wait_for_master=wait_for_master,
            )
        working_dir, staging_ignore_parts = self._resolve_run_workspace(request)
        # Own the staging/home identity before the first cleanup/prepare step.
        # ACP process creation awaits later; without this reservation, another
        # concurrent Cursor start can mistake this still-starting session for
        # stale runtime state and delete it.
        self._preparing_ids.add(request.agent_session_id)
        self._claim_runtime_id(request.agent_session_id)
        staging_root = self._staging_root_for(
            working_dir,
            read_only=request.read_only,
        )
        try:
            staging_dir, baseline_snapshot = await self._prepare_staging_workspace_cancellation_safe(
                request.agent_session_id,
                working_dir,
                ignore_parts=staging_ignore_parts,
                stable_key=request.conversation_id if request.read_only else None,
                staging_root=staging_root,
            )
        except BaseException:
            self._preparing_ids.discard(request.agent_session_id)
            self._release_runtime_id(request.agent_session_id)
            raise
        provider_request = replace(request, working_dir=staging_dir)
        try:
            # Cursor always works from an isolated staging copy, never the real
            # workspace. Nirai-root read-only review stages outside the repository
            # specifically so the entire real root can be denied without also
            # denying the provider cwd.
            extra_denied_paths = (working_dir,)
            persistent_conversation_home = (
                request.read_only
                and isinstance(request.conversation_id, str)
                and bool(request.conversation_id.strip())
            )
            cursor_home = self._prepare_cursor_home(
                request.agent_session_id,
                working_dir=staging_dir,
                extra_denied_paths=extra_denied_paths,
                stable_key=request.conversation_id if persistent_conversation_home else None,
            )
        except Exception:
            try:
                self._cleanup_staging_workspace(staging_dir)
            finally:
                self._preparing_ids.discard(request.agent_session_id)
                self._release_runtime_id(request.agent_session_id)
            raise
        client: _CursorAcpClient | None = None
        message_chunks: list[str] = []
        replaying_history = False
        provider_quiesced = False
        provider_work_succeeded = False
        try:
            command_prefix = resolve_cursor_command()
        except BrainUnavailableError as exc:
            try:
                self._cleanup_cursor_home_after_turn(
                    cursor_home,
                    persistent=persistent_conversation_home,
                )
                self._cleanup_staging_workspace(staging_dir)
            finally:
                self._preparing_ids.discard(request.agent_session_id)
                self._release_runtime_id(request.agent_session_id)
            raise AgentRuntimeUnavailableError(str(exc)) from exc

        async def emit_normalized(event_type: str, payload: dict[str, Any]) -> None:
            await emit(event_type, payload)

        async def handle_notification(message: dict[str, Any]) -> None:
            method = message.get("method")
            params = message.get("params")
            if replaying_history:
                # session/load may replay transcript, todo, task, tool and other
                # historical notifications. They restore Provider-native state;
                # none are output from the new Nirai turn, so never re-emit them.
                return
            if method == "session/update" and isinstance(params, dict):
                update = params.get("update")
                text = cursor_message_chunk_text(update)
                if text:
                    message_chunks.append(text)
                for event_type, payload in normalize_cursor_session_update(
                    update,
                    working_dir=staging_dir,
                    workspace_policy=self.workspace_policy,
                ):
                    # Cursor workspace edits are intentionally hidden until the
                    # staging diff is reviewed. The real Task workspace must not
                    # appear modified before Master approval.
                    if event_type in {"file_change", "artifact"}:
                        continue
                    if event_type == "command_execution":
                        payload = {**payload, "cwd": str(working_dir), "execution_scope": "cursor_staging"}
                    await emit_normalized(event_type, payload)
                return
            if method == "cursor/update_todos":
                for event_type, payload in normalize_cursor_todos(params):
                    await emit_normalized(event_type, payload)
                return
            if method == "cursor/task":
                for event_type, payload in normalize_cursor_task(params):
                    await emit_normalized(event_type, payload)
                return
            if method == "cursor/generate_image":
                try:
                    events = normalize_cursor_image(
                        params,
                        working_dir=staging_dir,
                        workspace_policy=self.workspace_policy,
                    )
                except AgentSafetyError as exc:
                    await emit("error", {
                        "code": "cursor_artifact_outside_workspace",
                        "message": str(exc),
                        "recoverable": False,
                    })
                    return
                for event_type, payload in events:
                    if event_type == "artifact":
                        continue
                    await emit_normalized(event_type, payload)

        async def handle_request(message: dict[str, Any]) -> dict[str, Any]:
            method = message.get("method")
            params = message.get("params")
            if replaying_history:
                async def suppress_replay_event(_event_type: str, _payload: dict[str, Any]) -> None:
                    return None

                extension_result = await self._handle_notification_extension_request(
                    method,
                    params,
                    emit=suppress_replay_event,
                )
                if extension_result is not None:
                    return extension_result
                if method == "session/request_permission":
                    provider_options = (
                        params.get("options")
                        if isinstance(params, dict) and isinstance(params.get("options"), list)
                        else None
                    )
                    return _permission_reject_result(provider_options)
                if method == "cursor/ask_question":
                    return {"outcome": {"outcome": "skipped", "reason": "historical replay"}}
                if method == "cursor/create_plan":
                    return {"outcome": {"outcome": "accepted"}}
            extension_result = await self._handle_notification_extension_request(
                method,
                params,
                emit=emit,
            )
            if extension_result is not None:
                return extension_result
            if request.read_only:
                if method == "session/request_permission":
                    return await self._handle_read_only_permission_request(
                        params,
                        request=provider_request,
                        emit=emit,
                    )
                if method == "cursor/ask_question":
                    await emit("status_message", {
                        "kind": "cursor_read_only_question_skipped",
                        "text": "Cursor question skipped by read-only supervisor policy",
                    })
                    return {"outcome": {"outcome": "skipped", "reason": "read-only conversation"}}
                if method == "cursor/create_plan":
                    await emit("status_message", {
                        "kind": "cursor_read_only_plan_auto_accepted",
                        "text": "Cursor plan accepted as a read-only planning step",
                    })
                    return {"outcome": {"outcome": "accepted"}}
            if method == "session/request_permission":
                return await self._handle_permission_request(
                    params,
                    request=provider_request,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            if method == "cursor/ask_question":
                return await self._handle_question_request(params, emit=emit, wait_for_master=wait_for_master)
            if method == "cursor/create_plan":
                return await self._handle_plan_request(params, emit=emit, wait_for_master=wait_for_master)
            raise AgentRuntimeProtocolError(f"Unsupported Cursor ACP client request: {method}")

        process: asyncio.subprocess.Process | None = None
        try:
            cursor_environment = self._build_cursor_environment(cursor_home)
            # The staged copy intentionally has no .git directory. Keep Git
            # discovery from escaping upward into the real Nirai repository.
            cursor_environment["GIT_CEILING_DIRECTORIES"] = str(staging_dir)
            process = await asyncio.create_subprocess_exec(
                *command_prefix,
                "acp",
                cwd=str(staging_dir),
                env=cursor_environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                creationflags=_windows_subprocess_flags(),
            )
            client = _CursorAcpClient(
                process,
                request_handler=handle_request,
                notification_handler=handle_notification,
            )
            async with self._active_lock:
                self._active[request.agent_session_id] = _ActiveCursorSession(client=client)
                self._preparing_ids.discard(request.agent_session_id)

            initialized = await client.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "nirai", "version": "0.1.0"},
            })
            auth_methods = initialized.get("authMethods")
            if not _contains_cursor_login(auth_methods):
                raise AgentRuntimeProtocolError("Cursor ACP did not advertise cursor_login authentication")
            await client.request("authenticate", {"methodId": "cursor_login"})
            session_params = {
                "cwd": str(staging_dir),
                "mcpServers": [],
            }
            provider_session_id = request.provider_session_id
            resumed_native_session = False
            if provider_session_id is not None:
                capabilities = initialized.get("agentCapabilities")
                capabilities = capabilities if isinstance(capabilities, dict) else {}
                session_capabilities = capabilities.get("sessionCapabilities")
                session_capabilities = (
                    session_capabilities if isinstance(session_capabilities, dict) else {}
                )
                if "resume" in session_capabilities:
                    await client.request(
                        "session/resume",
                        {"sessionId": provider_session_id, **session_params},
                    )
                    session = {}
                    resumed_native_session = True
                elif capabilities.get("loadSession") is True:
                    replaying_history = True
                    try:
                        session = await client.request(
                            "session/load",
                            {"sessionId": provider_session_id, **session_params},
                        )
                    finally:
                        replaying_history = False
                    resumed_native_session = True
                else:
                    raise AgentRuntimeProtocolError(
                        "Cursor ACP cannot resume the existing Nirai Conversation session"
                    )
            else:
                session = await client.request("session/new", session_params)
                provider_session_id = session.get("sessionId")
                if not isinstance(provider_session_id, str) or not provider_session_id:
                    raise AgentRuntimeProtocolError("Cursor ACP session/new returned no sessionId")
            async with self._active_lock:
                active = self._active.get(request.agent_session_id)
                if active is not None:
                    active.provider_session_id = provider_session_id

            config_options = session.get("configOptions") if isinstance(session, dict) else None
            if not resumed_native_session or isinstance(config_options, list):
                await self._configure_session(
                    client,
                    provider_session_id,
                    config_options,
                    requested_model=request.model,
                    requested_reasoning=request.reasoning_effort,
                    read_only=request.read_only,
                )
            await emit("run_state", {
                "state": "running",
                "provider_session_id": provider_session_id,
            })
            await emit("status_message", {
                "kind": "provider_session_started",
                "text": "Cursor ACP session started",
            })

            prompt_result = await client.request(
                "session/prompt",
                {
                    "sessionId": provider_session_id,
                    "prompt": [{"type": "text", "text": self._build_agent_prompt(provider_request)}],
                },
                timeout_sec=60.0 * 60.0,
            )
            stop_reason = prompt_result.get("stopReason")
            if stop_reason == "cancelled":
                await emit("run_state", {"state": "cancelled"})
                return None
            if stop_reason not in {None, "end_turn"}:
                raise AgentRuntimeError(f"Cursor ACP turn stopped unexpectedly: {stop_reason}")

            summary = "".join(message_chunks).strip()

            # Cursor can edit its workspace without emitting an ACP permission
            # request. Quiesce the provider first, then review the isolated
            # staging diff and only apply it to the real Task workspace after a
            # Nirai-owned Master approval.
            if process is not None and not await _stop_process_tree(process):
                raise AgentRuntimeError("Cursor ACP process could not be stopped before staged review")
            if client is not None:
                await client.close()
            provider_quiesced = True
            try:
                await self._complete_staged_work(
                    request,
                    working_dir=working_dir,
                    staging_dir=staging_dir,
                    cursor_home=cursor_home,
                    baseline=baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            except StagedApplyCancelledAfterCommit:
                provider_work_succeeded = True
                return AgentRunResult(
                    summary=summary or "Cursor Agent completed the task",
                    work_committed=True,
                )

            provider_work_succeeded = True
            if summary:
                await emit("assistant_message", {
                    "phase": "completed",
                    "message_phase": "final_answer",
                    "text": summary,
                })
            return summary or "Cursor Agent completed the task"
        except asyncio.CancelledError:
            if client is not None:
                active = self._active.get(request.agent_session_id)
                if active is not None and active.provider_session_id:
                    try:
                        await client.notify("session/cancel", {"sessionId": active.provider_session_id})
                    except Exception:
                        LOGGER.debug("cursor_acp_cancel_notify_failed", exc_info=True)
            raise
        except AgentProviderLimitError as exc:
            if process is not None and not await _stop_process_tree(process):
                raise AgentRuntimeUnavailableError(
                    "Cursor ACP process could not be stopped after provider limit"
                ) from exc
            if client is not None:
                await client.close()
            provider_quiesced = True
            partial_path = None
            if not request.read_only:
                partial_path = await self._preserve_partial_staged_changes_cancellation_safe(
                    request,
                    working_dir=working_dir,
                    staging_dir=staging_dir,
                    baseline=baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                    reason=exc.code,
                )
            raise exc.with_partial_work(partial_path) from exc
        except (AgentRuntimeError, AgentSafetyError):
            raise
        except (OSError, RuntimeError) as exc:
            raise AgentRuntimeUnavailableError(f"Cursor ACP could not start: {exc}") from exc
        finally:
            async with self._active_lock:
                self._active.pop(request.agent_session_id, None)
                self._preparing_ids.discard(request.agent_session_id)
            self._release_runtime_id(request.agent_session_id)
            cleanup_errors: list[str] = []
            if not provider_quiesced:
                # Stop the Windows process tree while the ACP parent PID is still
                # alive. Closing stdin first can let Cursor exit before taskkill /T
                # and orphan provider helper processes.
                if process is not None:
                    if not await _stop_process_tree(process):
                        cleanup_errors.append("Cursor ACP process could not be stopped")
                if client is not None:
                    try:
                        await client.close()
                    except Exception as exc:
                        cleanup_errors.append(f"ACP client close failed: {_bounded_text(exc, 300)}")
            await asyncio.sleep(0)
            try:
                self._cleanup_cursor_home_after_turn(
                    cursor_home,
                    persistent=persistent_conversation_home,
                )
            except AgentRuntimeError as exc:
                cleanup_errors.append(str(exc))
            try:
                self._cleanup_staging_workspace(staging_dir)
            except AgentRuntimeError as exc:
                cleanup_errors.append(str(exc))
            await self._report_cleanup_errors(
                request, cleanup_errors, provider_work_succeeded=provider_work_succeeded, emit=emit,
            )

    async def _run_exact_cli(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None | AgentRunResult:
        """Run CLI-only Cursor models without downgrading them through ACP.

        The CLI is never pointed at the real Task workspace. Writable work runs
        in Nirai's existing staging copy with Shell/Web/MCP denied explicitly;
        the frozen diff then goes through the same Master approval/apply path as
        ACP. Read-only review/consult uses Cursor ask mode and is verified
        unchanged after the provider exits.
        """
        working_dir, staging_ignore_parts = self._resolve_run_workspace(request)

        self._cli_active_ids.add(request.agent_session_id)
        self._claim_runtime_id(request.agent_session_id)
        staging_dir: Path | None = None
        cursor_home: Path | None = None
        persistent_conversation_home = (
            request.read_only
            and isinstance(request.conversation_id, str)
            and bool(request.conversation_id.strip())
        )
        provider_work_succeeded = False
        try:
            staging_dir, baseline_snapshot = await self._prepare_staging_workspace_cancellation_safe(
                request.agent_session_id,
                working_dir,
                ignore_parts=staging_ignore_parts,
                stable_key=request.conversation_id if request.read_only else None,
                staging_root=self._staging_root_for(
                    working_dir,
                    read_only=request.read_only,
                ),
            )
            provider_request = replace(request, working_dir=staging_dir)
            extra_denied_paths = (working_dir,)
            cursor_home = self._prepare_cursor_home(
                request.agent_session_id,
                working_dir=staging_dir,
                extra_denied_paths=extra_denied_paths,
                stable_key=request.conversation_id if persistent_conversation_home else None,
            )
            self._harden_cursor_cli_home(cursor_home)
            try:
                command_prefix = resolve_cursor_command()
            except BrainUnavailableError as exc:
                raise AgentRuntimeUnavailableError(str(exc)) from exc

            argv = [*command_prefix, "-p"]
            if request.provider_session_id is not None:
                argv.extend(["--resume", request.provider_session_id])
            if request.model:
                argv.extend(["--model", request.model])
            if request.read_only:
                argv.extend(["--mode", "ask"])
            else:
                # Write permission is safe only inside staging. Explicit CLI
                # denies added above still win over --force, notably Shell(*)
                # and every real/out-of-scope path.
                argv.append("--force")
            argv.extend([
                "--trust",
                "--output-format",
                "json",
                "--workspace",
                str(staging_dir),
            ])
            environment = self._build_cursor_environment(cursor_home)
            environment["GIT_CEILING_DIRECTORIES"] = str(staging_dir)
            environment["CURSOR_INVOKED_AS"] = "cursor-agent.cmd"
            environment["NODE_COMPILE_CACHE"] = str(cursor_home / "node-compile-cache")

            await emit("run_state", {"state": "running"})
            await emit("status_message", {
                "kind": "provider_process_started",
                "text": "Cursor exact-model CLI process started",
            })
            completed = await self._cli_process_manager.run(
                request.agent_session_id,
                argv,
                cwd=staging_dir,
                timeout_sec=CURSOR_EXACT_CLI_TIMEOUT_SEC,
                stdin_text=self._build_exact_cli_prompt(provider_request),
                env=environment,
            )
            if completed.returncode != 0:
                if request.agent_session_id in self._cancel_intent_ids:
                    raise asyncio.CancelledError
                detail = _extract_cli_error(completed)
                provider_limit = classify_provider_limit(detail)
                if provider_limit is not None:
                    raise provider_limit
                if _is_unavailable_error(detail):
                    raise AgentRuntimeUnavailableError(f"Cursor Agent is unavailable: {detail}")
                raise AgentRuntimeProtocolError(f"Cursor Agent CLI failed: {detail}")
            try:
                payload = json.loads(completed.stdout.strip())
            except json.JSONDecodeError as exc:
                raise AgentRuntimeProtocolError("Cursor Agent CLI returned invalid JSON") from exc
            if not isinstance(payload, dict):
                raise AgentRuntimeProtocolError("Cursor Agent CLI returned a non-object result")
            if payload.get("is_error") is True:
                detail = payload.get("result") or payload.get("error") or "Cursor Agent reported an error"
                provider_limit = classify_provider_limit(detail)
                if provider_limit is not None:
                    raise provider_limit
                raise AgentRuntimeProtocolError(str(detail))

            summary_value = payload.get("result")
            summary = summary_value.strip() if isinstance(summary_value, str) else ""
            if request.read_only and _is_cursor_review_request(request):
                summary = _normalize_cursor_review_summary(summary)
            provider_session_id = payload.get("session_id")
            if not isinstance(provider_session_id, str) or not provider_session_id.strip():
                raise AgentRuntimeProtocolError("Cursor Agent CLI returned no resumable session_id")
            provider_session_id = provider_session_id.strip()
            if (
                request.provider_session_id is not None
                and provider_session_id != request.provider_session_id
            ):
                raise AgentRuntimeProtocolError("Cursor Agent --resume returned a different session_id")

            await emit("run_state", {
                "state": "running",
                "provider_session_id": provider_session_id,
            })
            await emit("status_message", {
                "kind": "provider_session_resumed" if request.provider_session_id else "provider_session_started",
                "text": "Cursor exact-model CLI session resumed" if request.provider_session_id else "Cursor exact-model CLI session started",
            })

            try:
                await self._complete_staged_work(
                    request,
                    working_dir=working_dir,
                    staging_dir=staging_dir,
                    cursor_home=cursor_home,
                    baseline=baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            except StagedApplyCancelledAfterCommit:
                provider_work_succeeded = True
                return AgentRunResult(
                    summary=summary or "Cursor Agent completed the task",
                    work_committed=True,
                )

            provider_work_succeeded = True
            if summary:
                await emit("assistant_message", {
                    "phase": "completed",
                    "message_phase": "final_answer",
                    "text": summary,
                })
            return summary or "Cursor Agent completed the task"
        except AgentProviderLimitError as exc:
            partial_path = None
            if not request.read_only and staging_dir is not None:
                partial_path = await self._preserve_partial_staged_changes_cancellation_safe(
                    request,
                    working_dir=working_dir,
                    staging_dir=staging_dir,
                    baseline=baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                    reason=exc.code,
                )
            raise exc.with_partial_work(partial_path) from exc
        finally:
            self._cli_active_ids.discard(request.agent_session_id)
            self._preparing_ids.discard(request.agent_session_id)
            self._release_runtime_id(request.agent_session_id)
            cleanup_errors: list[str] = []
            if cursor_home is not None:
                try:
                    self._cleanup_cursor_home_after_turn(
                        cursor_home,
                        persistent=persistent_conversation_home,
                    )
                except AgentRuntimeError as exc:
                    cleanup_errors.append(str(exc))
            if staging_dir is not None:
                try:
                    self._cleanup_staging_workspace(staging_dir)
                except AgentRuntimeError as exc:
                    cleanup_errors.append(str(exc))
            await self._report_cleanup_errors(
                request, cleanup_errors, provider_work_succeeded=provider_work_succeeded, emit=emit,
            )

    def _resolve_run_workspace(self, request: AgentRunRequest) -> tuple[Path, frozenset[str]]:
        working_dir = request.working_dir.resolve()
        if request.read_only:
            self.workspace_policy.resolve_read_only_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = self._read_only_staging_ignore_parts(working_dir)
        elif request.purpose == "integrated_audit":
            self.workspace_policy.resolve_integrated_audit_working_dir(
                str(working_dir),
                task_id=request.task_id,
            )
            staging_ignore_parts = self._writable_staging_ignore_parts(
                working_dir,
                integrated_audit=True,
            )
        else:
            self.workspace_policy.resolve_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = self._writable_staging_ignore_parts(working_dir)
        return working_dir, staging_ignore_parts

    async def _complete_staged_work(
        self,
        request: AgentRunRequest,
        *,
        working_dir: Path,
        staging_dir: Path,
        cursor_home: Path,
        baseline: dict[str, tuple[int, str]],
        ignore_parts: frozenset[str],
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> None:
        """Verify the quiesced provider output before declaring its work complete."""
        if request.read_only:
            await asyncio.to_thread(
                self._verify_read_only_review_unchanged,
                working_dir,
                staging_dir,
                baseline,
                ignore_parts=ignore_parts,
            )
        else:
            await self._review_and_apply_staged_changes(
                request,
                staging_dir=staging_dir,
                review_dir=cursor_home / ".nirai-staged-review",
                baseline=baseline,
                ignore_parts=ignore_parts,
                emit=emit,
                wait_for_master=wait_for_master,
            )

    async def _report_cleanup_errors(
        self,
        request: AgentRunRequest,
        cleanup_errors: list[str],
        *,
        provider_work_succeeded: bool,
        emit: EmitEvent,
    ) -> None:
        if not cleanup_errors:
            return
        message = "; ".join(cleanup_errors)
        if not provider_work_succeeded:
            raise AgentRuntimeError(message)
        try:
            await emit("error", {
                "code": "provider_cleanup_failed",
                "message": message,
                "recoverable": False,
            })
        except Exception:
            LOGGER.warning(
                "cursor_cleanup_error_event_failed agent_session_id=%s error=%s",
                request.agent_session_id,
                _bounded_text(message, 500),
                exc_info=True,
            )

    def mark_cancel_intent(self, agent_session_id: str) -> None:
        self._cancel_intent_ids.add(agent_session_id)

    def clear_cancel_intent(self, agent_session_id: str) -> None:
        self._cancel_intent_ids.discard(agent_session_id)

    async def cancel(self, agent_session_id: str) -> bool:
        if await self._cli_process_manager.cancel(agent_session_id):
            return True
        async with self._active_lock:
            active = self._active.get(agent_session_id)
        if active is None:
            return False
        if active.provider_session_id is None:
            return True
        try:
            await active.client.notify("session/cancel", {"sessionId": active.provider_session_id})
        except AgentRuntimeError:
            LOGGER.warning("cursor_acp_session_cancel_failed agent_session_id=%s", agent_session_id, exc_info=True)
        return True

    async def _configure_session(
        self,
        client: _CursorAcpClient,
        session_id: str,
        raw_options: object,
        *,
        requested_model: str | None,
        requested_reasoning: str | None,
        read_only: bool,
    ) -> None:
        options = raw_options if isinstance(raw_options, list) else []
        mode = _config_by_category(options, "mode")
        if mode is not None:
            await client.request("session/set_config_option", {
                "sessionId": session_id,
                "configId": mode["id"],
                "value": "ask" if read_only else "agent",
            })

        if requested_model:
            model = _config_by_category(options, "model")
            if model is None:
                raise AgentRuntimeProtocolError("Cursor ACP does not expose a model config option")
            model_value = _resolve_select_value(model, requested_model)
            if model_value is None:
                raise AgentRuntimeProtocolError(
                    "Cursor ACP cannot represent the selected Resident model exactly: "
                    f"{requested_model}. Choose Auto or an ACP-compatible Cursor model; "
                    "Nirai will not silently downgrade reasoning effort."
                )
            response = await client.request("session/set_config_option", {
                "sessionId": session_id,
                "configId": model["id"],
                "value": model_value,
            })
            options = response.get("configOptions") if isinstance(response.get("configOptions"), list) else options

        if requested_reasoning:
            reasoning = _config_by_category(options, "thought_level")
            if reasoning is None:
                raise AgentRuntimeProtocolError(
                    "Cursor ACP does not expose a separate reasoning config option; "
                    "Nirai will not ignore the requested reasoning effort"
                )
            reasoning_value = _resolve_select_value(reasoning, requested_reasoning)
            if reasoning_value is None:
                raise AgentRuntimeProtocolError(
                    f"Cursor ACP reasoning value is unavailable: {requested_reasoning}"
                )
            await client.request("session/set_config_option", {
                "sessionId": session_id,
                "configId": reasoning["id"],
                "value": reasoning_value,
            })

    async def _handle_read_only_permission_request(
        self,
        params: object,
        *,
        request: AgentRunRequest,
        emit: EmitEvent,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return _permission_reject_result(None)
        tool_call = params.get("toolCall")
        provider_options = params.get("options") if isinstance(params.get("options"), list) else []
        tool_kind = ""
        title = "Cursor review tool"
        if isinstance(tool_call, dict):
            tool_kind = str(tool_call.get("kind") or "").casefold()
            raw_title = tool_call.get("title")
            if isinstance(raw_title, str) and raw_title:
                title = raw_title

        common_kind = _common_permission_kind(tool_kind)
        if _is_external_tool(tool_kind, title) or common_kind in {"file_change", "command"}:
            await emit("status_message", {
                "kind": "cursor_review_tool_rejected",
                "text": f"Cursor review tool rejected by read-only policy: {title}",
            })
            return _permission_reject_result(provider_options)

        try:
            validate_cursor_tool_paths(
                tool_call,
                working_dir=request.working_dir,
                workspace_policy=self.workspace_policy,
            )
        except AgentSafetyError as exc:
            await emit("error", {
                "code": "cursor_review_tool_outside_workspace",
                "message": str(exc),
                "recoverable": False,
            })
            return _permission_reject_result(provider_options)

        option_id = _permission_option_by_kind(provider_options, "allow_once")
        if option_id is None:
            await emit("status_message", {
                "kind": "cursor_review_tool_rejected",
                "text": f"Cursor review tool has no safe one-shot permission: {title}",
            })
            return _permission_reject_result(provider_options)
        await emit("status_message", {
            "kind": "cursor_review_read_allowed",
            "text": f"Cursor review read-only tool allowed once: {title}",
        })
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

    async def _handle_permission_request(
        self,
        params: object,
        *,
        request: AgentRunRequest,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return _permission_reject_result(None)
        tool_call = params.get("toolCall")
        tool_kind = ""
        tool_call_id = None
        title = "Cursor tool requires approval"
        if isinstance(tool_call, dict):
            tool_kind = str(tool_call.get("kind") or "").casefold()
            raw_id = tool_call.get("toolCallId")
            tool_call_id = raw_id if isinstance(raw_id, str) and raw_id else None
            raw_title = tool_call.get("title")
            if isinstance(raw_title, str) and raw_title:
                title = raw_title

        provider_options = params.get("options") if isinstance(params.get("options"), list) else []
        if _is_external_tool(tool_kind, title):
            await emit("status_message", {
                "kind": "external_tool_blocked",
                "text": f"Cursor external tool blocked by Nirai baseline: {title}",
            })
            return _permission_reject_result(provider_options)

        try:
            validate_cursor_tool_paths(
                tool_call,
                working_dir=request.working_dir,
                workspace_policy=self.workspace_policy,
            )
        except AgentSafetyError as exc:
            await emit("error", {
                "code": "cursor_tool_outside_workspace",
                "message": str(exc),
                "recoverable": False,
            })
            return _permission_reject_result(provider_options)

        request_id = tool_call_id or f"cursor-permission-{id(params)}"
        common_kind = _common_permission_kind(tool_kind)
        if common_kind == "file_change":
            paths = cursor_permission_paths(tool_call)
            if not paths:
                await emit("error", {
                    "code": "cursor_file_change_path_unknown",
                    "message": "Cursor file change did not expose a path Nirai can validate",
                    "recoverable": False,
                })
                return _permission_reject_result(provider_options)
            if isinstance(tool_call, dict):
                synthetic_update = {**tool_call, "sessionUpdate": "tool_call", "toolCallId": request_id}
                for event_type, event_payload in normalize_cursor_session_update(
                    synthetic_update,
                    working_dir=request.working_dir,
                    workspace_policy=self.workspace_policy,
                ):
                    if event_type == "file_change":
                        await emit(event_type, event_payload)

        # Local, path-validated reads/writes happen only in Nirai's isolated
        # staging workspace. They do not warrant a Master stop; the frozen diff
        # is still validated before any real-workspace apply.
        if common_kind in {"file_change", "read"}:
            option_id = _permission_option_by_kind(provider_options, "allow_once")
            if option_id is not None:
                await emit("status_message", {
                    "kind": "cursor_tool_auto_allowed",
                    "text": f"Cursor local staging tool allowed automatically: {title}",
                })
                return {"outcome": {"outcome": "selected", "optionId": option_id}}
            return _permission_reject_result(provider_options)

        # Shell/terminal cannot be safely path-confined. Do not interrupt the
        # Master for these; reject and let Holo run trusted project commands via
        # Local MCP when needed.
        if common_kind == "command":
            await emit("status_message", {
                "kind": "cursor_command_blocked",
                "text": f"Cursor command blocked by Nirai staging policy: {title}",
            })
            return _permission_reject_result(provider_options)

        # Unknown permission kinds fail closed instead of surfacing low-value
        # approval prompts to Master.
        await emit("status_message", {
            "kind": "cursor_tool_blocked",
            "text": f"Cursor tool blocked by Nirai policy: {title}",
        })
        return _permission_reject_result(provider_options)

    async def _handle_notification_extension_request(
        self,
        method: object,
        params: object,
        *,
        emit: EmitEvent,
    ) -> dict[str, Any] | None:
        """Accept Cursor extension drift where documented notifications arrive as requests.

        Cursor documents task/todo/image as notifications, but current Windows ACP
        builds can attach a JSON-RPC id and block waiting for the documented response
        shape. Handle only extensions Nirai already recognizes; unknown methods still
        fail closed in the caller.
        """
        if method == "cursor/update_todos":
            if not isinstance(params, dict) or not isinstance(params.get("todos"), list):
                return {"outcome": {"outcome": "rejected", "reason": "Invalid Cursor todo update"}}
            for event_type, payload in normalize_cursor_todos(params):
                await emit(event_type, payload)
            return {
                "outcome": {
                    "outcome": "accepted",
                    "todos": params["todos"],
                }
            }
        if method == "cursor/task":
            if not isinstance(params, dict) or not isinstance(params.get("toolCallId"), str):
                return {"outcome": {"outcome": "rejected", "reason": "Invalid Cursor task update"}}
            for event_type, payload in normalize_cursor_task(params):
                await emit(event_type, payload)
            completed: dict[str, Any] = {"outcome": "completed"}
            agent_id = params.get("agentId")
            duration_ms = params.get("durationMs")
            if isinstance(agent_id, str) and agent_id:
                completed["agentId"] = agent_id
            if isinstance(duration_ms, int) and not isinstance(duration_ms, bool) and duration_ms >= 0:
                completed["durationMs"] = duration_ms
            return {"outcome": completed}
        if method == "cursor/generate_image":
            return {
                "outcome": {
                    "outcome": "rejected",
                    "reason": "Nirai does not expose Cursor image generation through Agent Runtime",
                }
            }
        return None

    async def _handle_question_request(
        self,
        params: object,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {"outcome": {"outcome": "skipped", "reason": "Invalid Cursor question"}}
        request_id = params.get("toolCallId")
        if not isinstance(request_id, str) or not request_id:
            request_id = f"cursor-question-{id(params)}"
        questions: list[dict[str, Any]] = []
        raw_questions = params.get("questions")
        if isinstance(raw_questions, list):
            for raw in raw_questions:
                if not isinstance(raw, dict):
                    continue
                question_id = raw.get("id")
                prompt = raw.get("prompt")
                if not isinstance(question_id, str) or not isinstance(prompt, str):
                    continue
                options: list[dict[str, Any]] = []
                raw_options = raw.get("options")
                if isinstance(raw_options, list):
                    for option in raw_options:
                        if not isinstance(option, dict):
                            continue
                        option_id = option.get("id")
                        label = option.get("label")
                        if isinstance(option_id, str) and isinstance(label, str):
                            options.append({"id": option_id, "label": label})
                questions.append({
                    "id": question_id,
                    "question": prompt,
                    "options": options,
                    "allow_multiple": raw.get("allowMultiple") is True,
                    "allow_free_text": False,
                })
        payload = {
            "request_id": request_id,
            "title": params.get("title") if isinstance(params.get("title"), str) else "Cursor question",
            "questions": questions,
        }
        await emit("question_request", payload)
        response = await wait_for_master(request_id, "question", payload)
        answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(answers, dict):
            return {"outcome": {"outcome": "skipped"}}
        mapped: list[dict[str, Any]] = []
        for question in questions:
            question_id = question["id"]
            raw_values = answers.get(question_id)
            values = raw_values if isinstance(raw_values, list) else []
            selected: list[str] = []
            option_by_label = {
                option["label"].casefold(): option["id"]
                for option in question["options"]
                if isinstance(option.get("label"), str) and isinstance(option.get("id"), str)
            }
            option_ids = {
                option["id"]
                for option in question["options"]
                if isinstance(option.get("id"), str)
            }
            for value in values:
                if not isinstance(value, str):
                    continue
                if value in option_ids:
                    selected.append(value)
                elif value.casefold() in option_by_label:
                    selected.append(option_by_label[value.casefold()])
            mapped.append({"questionId": question_id, "selectedOptionIds": selected})
        return {"outcome": {"outcome": "answered", "answers": mapped}}

    async def _handle_plan_request(
        self,
        params: object,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return {"outcome": {"outcome": "rejected", "reason": "Invalid Cursor plan"}}
        request_id = params.get("toolCallId")
        if not isinstance(request_id, str) or not request_id:
            request_id = f"cursor-plan-{id(params)}"
        plan = params.get("plan") if isinstance(params.get("plan"), str) else ""
        todos = params.get("todos") if isinstance(params.get("todos"), list) else []
        steps = [
            {
                "id": todo.get("id"),
                "step": todo.get("content"),
                "status": todo.get("status"),
            }
            for todo in todos
            if isinstance(todo, dict)
        ]
        payload = {
            "request_id": request_id,
            "markdown": plan,
            "text": plan,
            "steps": steps,
            "approval_required": False,
            "name": params.get("name"),
            "overview": params.get("overview"),
        }
        await emit("plan", payload)
        await emit("status_message", {
            "kind": "cursor_plan_auto_accepted",
            "text": "Cursor plan accepted automatically; destructive effects remain subject to Nirai safety gates",
        })
        return {"outcome": {"outcome": "accepted"}}

    def discard_conversation_context(self, conversation_id: str) -> None:
        cleaned = conversation_id.strip()
        if not cleaned:
            return
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()
        home_target = self.root / "runtime" / "cursor_conversation_homes" / f"CV-{digest[:32]}"
        staging_targets = {
            (
                self.workspace_policy.default_workspace_root
                / f".cursor-conversation-{digest[:24]}"
            ).resolve(),
            (
                self._staging_root_for(self.root, read_only=True)
                / f".cursor-conversation-{digest[:24]}"
            ).resolve(),
        }
        cleanup_errors: list[str] = []
        try:
            self._cleanup_cursor_home(home_target)
        except AgentRuntimeError as exc:
            cleanup_errors.append(str(exc))
        for staging_target in staging_targets:
            try:
                self._cleanup_staging_workspace(staging_target)
            except AgentRuntimeError as exc:
                cleanup_errors.append(str(exc))
        if cleanup_errors:
            raise AgentRuntimeError("; ".join(cleanup_errors))

    @staticmethod
    def _build_agent_prompt(request: AgentRunRequest) -> str:
        if request.read_only:
            review_mode = request.purpose == "review" or (
                request.purpose == "work" and request.task_id.startswith("HR-")
            )
            if review_mode:
                identity = "You are the read-only Cursor reviewer supervised by Nirai Holo."
            elif request.purpose == "resident_brain":
                identity = (
                    f"You are the read-only Cursor transport for Nirai Resident {request.resident}. "
                    "The Conversation input below is the authoritative Resident/persona/context prompt for this turn."
                )
            else:
                identity = "You are a read-only Cursor participant supervised by Nirai Holo."
            common = f"""{identity}
Use the current working directory only as read-only context.
Do not create, modify, move, or delete any file. Do not run shell or terminal commands.
Do not use user-level rules, skills, histories, MCP servers, external web access, Git push, system settings, or unrelated secrets.
Use code inspection only. Do not ask the Master for approval or tool questions; if clarification is useful, state it naturally in your final answer.
"""
            if review_mode:
                return f"""{common}This turn is an independent code review.
The first non-empty line of the final answer must be exactly SAFE or NEEDS FIX.
If NEEDS FIX, list concrete findings with priority, file, line or symbol, and reason. Do not fix them.
If SAFE, state briefly what was checked after the verdict line.

Review request:
{request.prompt.strip()}
"""
            purpose_text = {
                "consult": "This turn is a technical/specification consultation. Inspect relevant code when useful and answer the discussion directly.",
                "brainstorm": "This turn is a brainstorming discussion. Explore useful alternatives and trade-offs without making changes.",
                "talk": "This turn is a conversation. Respond directly and naturally without making changes.",
                "resident_brain": "This turn is an ordinary Nirai Resident Brain conversation. Follow the Conversation input exactly and return its requested response shape without making changes.",
            }.get(
                request.purpose,
                "This turn is a read-only consultation. Respond directly without making changes.",
            )
            return f"""{common}{purpose_text}

Conversation input:
{request.prompt.strip()}
"""
        return f"""You are the Agent Runtime worker for Nirai Resident {request.resident}.
Complete the Master task inside the current task working directory only.
Do not read or write outside the current working directory.
Do not use user-level rules, skills, histories, MCP servers, external web access, Git push, system settings, or unrelated secrets.
All tool operations that require permission must wait for the Master through the ACP client.
Do not bypass denied permissions or approval prompts.
Keep the final answer concise and report what was completed.

Task:
{request.prompt.strip()}
"""

    @staticmethod
    def _build_exact_cli_prompt(request: AgentRunRequest) -> str:
        if request.read_only:
            return CursorAcpAdapter._build_agent_prompt(request)
        return f"""You are the exact-model Cursor worker for Nirai Resident {request.resident}.
The current working directory is an isolated Nirai staging copy, not the real Task workspace.
Complete the Master task by reading and editing files inside this staging directory only.
Do not use shell or terminal commands. Do not access paths outside the current working directory.
Do not use user-level rules, histories, MCP servers, external web/browser access, Git, system settings, or unrelated secrets.
If a denied capability would be useful, continue with file inspection/editing instead of trying to bypass the denial.
Nirai will review the frozen staging diff and ask the Master before applying anything to the real workspace.
Keep the final answer concise and report what was completed.

Task:
{request.prompt.strip()}
"""


def _windows_subprocess_flags() -> int:
    import subprocess

    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


async def _stop_process_tree(process: asyncio.subprocess.Process) -> bool:
    tree_stop_ok = os.name != "nt"
    if os.name == "nt":
        # Always attempt taskkill /T, even if the ACP parent already reported an
        # exit. A helper process may outlive the parent; returning success merely
        # because returncode is set would incorrectly declare staging quiescent.
        try:
            killer = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_windows_subprocess_flags(),
                ),
                timeout=ACP_STOP_STEP_TIMEOUT_SEC,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=ACP_STOP_STEP_TIMEOUT_SEC)
                tree_stop_ok = killer.returncode == 0
            except asyncio.TimeoutError:
                try:
                    killer.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.wait_for(killer.wait(), timeout=ACP_STOP_STEP_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    pass
        except (asyncio.TimeoutError, OSError):
            LOGGER.warning("cursor_acp_taskkill_failed pid=%s", process.pid, exc_info=True)

    if process.returncode is not None:
        return tree_stop_ok
    try:
        await asyncio.wait_for(process.wait(), timeout=ACP_STOP_STEP_TIMEOUT_SEC)
        return process.returncode is not None and tree_stop_ok
    except asyncio.TimeoutError:
        pass
    for action in (process.terminate, process.kill):
        if process.returncode is not None:
            return tree_stop_ok
        try:
            action()
        except (ProcessLookupError, OSError):
            continue
        try:
            await asyncio.wait_for(process.wait(), timeout=ACP_STOP_STEP_TIMEOUT_SEC)
            return process.returncode is not None and tree_stop_ok
        except asyncio.TimeoutError:
            continue
    return process.returncode is not None and tree_stop_ok
