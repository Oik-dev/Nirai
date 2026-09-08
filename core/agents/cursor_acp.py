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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from ..brains.base import BrainUnavailableError
from ..brains.cursor import _extract_cli_error, _is_unavailable_error, resolve_cursor_command
from ..brains.process_manager import ProcessManager
from .base import (
    AgentRunRequest,
    EmitEvent,
    AgentRuntimeError,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    WaitForMaster,
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


LOGGER = logging.getLogger("nirai.core.agent.cursor_acp")
ACP_REQUEST_TIMEOUT_SEC = 30.0
ACP_STOP_STEP_TIMEOUT_SEC = 3.0
CURSOR_HOME_CLEANUP_RETRIES = 3
CURSOR_STAGE_CLEANUP_RETRIES = 3
CURSOR_STALE_RUNTIME_AGE_SEC = 6 * 60 * 60
CURSOR_STAGE_FILE_LIMIT = 20_000
CURSOR_STAGE_BYTE_LIMIT = 1_000_000_000
CURSOR_DIFF_TEXT_FILE_LIMIT = 1_000_000
CURSOR_EXACT_CLI_TIMEOUT_SEC = 60.0 * 60.0
CURSOR_EXTERNAL_TOOL_KINDS = {"search", "fetch", "web", "web_search", "web_fetch", "mcp"}
CURSOR_WRITABLE_IGNORE_NAMES = frozenset({
    ".cursor",
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    ".next",
    "coverage",
    "dist",
    "build",
    "out",
})
CURSOR_READ_ONLY_IGNORE_NAMES = CURSOR_WRITABLE_IGNORE_NAMES
CURSOR_NIRAI_REVIEW_IGNORE_NAMES = frozenset({
    ".tools",
    ".vrm",
    ".vrma",
    "runtime",
    "avatars",
    "material",
    "world_memory",
    ".env",
    ".env.*",
})

_ALLOWED_ENV_NAMES = {
    "APPDATA",
    "COMSPEC",
    "LOCALAPPDATA",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "WINDIR",
}


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


class CursorAcpAdapter:
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

    async def _prepare_staging_workspace_cancellation_safe(
        self,
        agent_session_id: str,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str],
        stable_key: str | None,
    ) -> tuple[Path, dict[str, tuple[int, str]]]:
        prepare_task = asyncio.create_task(
            asyncio.to_thread(
                self._prepare_staging_workspace,
                agent_session_id,
                working_dir,
                ignore_parts=ignore_parts,
                stable_key=stable_key,
            ),
            name=f"cursor-stage-prepare-{agent_session_id}",
        )
        try:
            return await asyncio.shield(prepare_task)
        except asyncio.CancelledError:
            # to_thread workers cannot be force-cancelled. Wait for a late stage
            # to materialize, remove it, then let the outer owner release claims.
            try:
                staging_dir, _baseline = await prepare_task
            except BaseException:
                staging_dir = None
            if staging_dir is not None:
                try:
                    await asyncio.to_thread(self._cleanup_staging_workspace, staging_dir)
                except AgentRuntimeError:
                    LOGGER.warning(
                        "cursor_cancelled_prepare_cleanup_failed agent_session_id=%s",
                        agent_session_id,
                        exc_info=True,
                    )
            raise

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        if _cursor_requires_exact_cli_model(request.model):
            return await self._run_exact_cli(
                request,
                emit=emit,
                wait_for_master=wait_for_master,
            )
        working_dir = request.working_dir.resolve()
        if request.read_only:
            self.workspace_policy.resolve_read_only_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = self._read_only_staging_ignore_parts(working_dir)
        else:
            self.workspace_policy.resolve_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = CURSOR_WRITABLE_IGNORE_NAMES
        # Own the staging/home identity before the first cleanup/prepare step.
        # ACP process creation awaits later; without this reservation, another
        # concurrent Cursor start can mistake this still-starting session for
        # stale runtime state and delete it.
        self._preparing_ids.add(request.agent_session_id)
        self._claim_runtime_id(request.agent_session_id)
        try:
            staging_dir, baseline_snapshot = await self._prepare_staging_workspace_cancellation_safe(
                request.agent_session_id,
                working_dir,
                ignore_parts=staging_ignore_parts,
                stable_key=request.conversation_id if request.read_only else None,
            )
        except BaseException:
            self._preparing_ids.discard(request.agent_session_id)
            self._release_runtime_id(request.agent_session_id)
            raise
        provider_request = AgentRunRequest(
            task_id=request.task_id,
            agent_session_id=request.agent_session_id,
            resident=request.resident,
            provider=request.provider,
            prompt=request.prompt,
            working_dir=staging_dir,
            model=request.model,
            reasoning_effort=request.reasoning_effort,
            read_only=request.read_only,
            purpose=request.purpose,
            conversation_id=request.conversation_id,
            provider_session_id=request.provider_session_id,
        )
        try:
            # For ordinary/external work, explicitly deny the real workspace so
            # Cursor can only see its isolated staging copy. Nirai-root review is
            # the one exception: the staging copy itself lives under that root,
            # so a root-wide deny would also make the staging cwd unreadable.
            # The fixed Nirai sensitive/source roots below remain denied at their
            # real paths, while the read-only prompt and final snapshot check
            # keep the review non-mutating.
            extra_denied_paths = (
                ()
                if request.read_only and working_dir == self.root
                else (working_dir,)
            )
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
            if method == "session/update" and isinstance(params, dict):
                if replaying_history:
                    # session/load replays old transcript/tool updates to the
                    # client. They are restoration traffic, not output from the
                    # new Nirai turn, so never append them to this turn summary
                    # or re-emit stale tool activity.
                    return
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
            process = await asyncio.create_subprocess_exec(
                *command_prefix,
                "acp",
                cwd=str(staging_dir),
                env=self._build_cursor_environment(cursor_home),
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
            if request.read_only:
                await asyncio.to_thread(
                    self._verify_read_only_review_unchanged,
                    working_dir,
                    staging_dir,
                    baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                )
            else:
                await self._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging_dir,
                    review_dir=cursor_home / ".nirai-staged-review",
                    baseline=baseline_snapshot,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )

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
            if cleanup_errors:
                raise AgentRuntimeError("; ".join(cleanup_errors))

    async def _run_exact_cli(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        """Run CLI-only Cursor models without downgrading them through ACP.

        The CLI is never pointed at the real Task workspace. Writable work runs
        in Nirai's existing staging copy with Shell/Web/MCP denied explicitly;
        the frozen diff then goes through the same Master approval/apply path as
        ACP. Read-only review/consult uses Cursor ask mode and is verified
        unchanged after the provider exits.
        """
        working_dir = request.working_dir.resolve()
        if request.read_only:
            self.workspace_policy.resolve_read_only_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = self._read_only_staging_ignore_parts(working_dir)
        else:
            self.workspace_policy.resolve_working_dir(str(working_dir), task_id=request.task_id)
            staging_ignore_parts = CURSOR_WRITABLE_IGNORE_NAMES

        self._cli_active_ids.add(request.agent_session_id)
        self._claim_runtime_id(request.agent_session_id)
        staging_dir: Path | None = None
        cursor_home: Path | None = None
        persistent_conversation_home = (
            request.read_only
            and isinstance(request.conversation_id, str)
            and bool(request.conversation_id.strip())
        )
        try:
            staging_dir, baseline_snapshot = await self._prepare_staging_workspace_cancellation_safe(
                request.agent_session_id,
                working_dir,
                ignore_parts=staging_ignore_parts,
                stable_key=request.conversation_id if request.read_only else None,
            )
            provider_request = AgentRunRequest(
                task_id=request.task_id,
                agent_session_id=request.agent_session_id,
                resident=request.resident,
                provider=request.provider,
                prompt=request.prompt,
                working_dir=staging_dir,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
                read_only=request.read_only,
                purpose=request.purpose,
                conversation_id=request.conversation_id,
                provider_session_id=request.provider_session_id,
            )
            extra_denied_paths = (
                ()
                if request.read_only and working_dir == self.root
                else (working_dir,)
            )
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
            environment["CURSOR_INVOKED_AS"] = "cursor-agent.cmd"
            environment["NODE_COMPILE_CACHE"] = str(cursor_home / "node-compile-cache")

            completed = await self._cli_process_manager.run(
                request.agent_session_id,
                argv,
                cwd=staging_dir,
                timeout_sec=CURSOR_EXACT_CLI_TIMEOUT_SEC,
                stdin_text=self._build_exact_cli_prompt(provider_request),
                env=environment,
            )
            if completed.returncode != 0:
                detail = _extract_cli_error(completed)
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

            if request.read_only:
                await asyncio.to_thread(
                    self._verify_read_only_review_unchanged,
                    working_dir,
                    staging_dir,
                    baseline_snapshot,
                    ignore_parts=staging_ignore_parts,
                )
            else:
                await self._review_and_apply_staged_changes(
                    request,
                    staging_dir=staging_dir,
                    review_dir=cursor_home / ".nirai-staged-review",
                    baseline=baseline_snapshot,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )

            if summary:
                await emit("assistant_message", {
                    "phase": "completed",
                    "message_phase": "final_answer",
                    "text": summary,
                })
            return summary or "Cursor Agent completed the task"
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
            if cleanup_errors:
                raise AgentRuntimeError("; ".join(cleanup_errors))

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
    ) -> None:
        options = raw_options if isinstance(raw_options, list) else []
        mode = _config_by_category(options, "mode")
        if mode is not None:
            await client.request("session/set_config_option", {
                "sessionId": session_id,
                "configId": mode["id"],
                "value": "agent",
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

        common_options = _common_permission_options(provider_options)
        payload: dict[str, Any] = {
            "request_id": request_id,
            "kind": common_kind,
            "title": title,
            "description": f"Cursor ACP tool kind: {tool_kind or 'other'}",
            "options": common_options,
        }
        if tool_call_id:
            payload["operation_id"] = tool_call_id
        if isinstance(tool_call, dict):
            raw_input = tool_call.get("rawInput")
            if isinstance(raw_input, dict):
                command = raw_input.get("command")
                if isinstance(command, str):
                    payload["command"] = command
                    payload["cwd"] = str(request.working_dir)
        await emit("approval_request", payload)
        response = await wait_for_master(request_id, "approval", payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        option_id = _permission_option_for_decision(provider_options, decision)
        if option_id is None:
            option_id = _permission_reject_option(provider_options)
        if option_id is None:
            return {"outcome": {"outcome": "cancelled"}}
        return {"outcome": {"outcome": "selected", "optionId": option_id}}

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
            "approval_required": True,
            "name": params.get("name"),
            "overview": params.get("overview"),
        }
        await emit("plan", payload)
        response = await wait_for_master(request_id, "plan", payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        reason = response.get("reason") if isinstance(response, dict) else None
        if decision == "approve":
            return {"outcome": {"outcome": "accepted"}}
        if decision == "cancel":
            return {"outcome": {"outcome": "cancelled"}}
        result: dict[str, Any] = {"outcome": "rejected"}
        if isinstance(reason, str) and reason:
            result["reason"] = reason
        return {"outcome": result}

    async def _review_and_apply_staged_changes(
        self,
        request: AgentRunRequest,
        *,
        staging_dir: Path,
        review_dir: Path,
        baseline: dict[str, tuple[int, str]],
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> None:
        changes, reviewed_staging, reviewed_bundle = await asyncio.to_thread(
            self._freeze_staged_changes,
            request.working_dir,
            staging_dir,
            review_dir,
            baseline,
        )
        if not changes:
            return

        operation_id = f"cursor-stage-apply-{request.agent_session_id}"
        review_changes = _cursor_review_manifest(changes)
        file_payload = {
            "operation_id": operation_id,
            "phase": "staged",
            "status": "pending_approval",
            "changes": review_changes,
        }
        await emit("file_change", file_payload)
        approval_payload = {
            "request_id": operation_id,
            "operation_id": operation_id,
            "kind": "file_change",
            "title": "Cursor staged changes are ready to apply",
            "description": (
                "Cursor worked only in an isolated staging workspace. "
                "Apply the reviewed changes to the real Task workspace?"
            ),
            "grant_root": str(request.working_dir),
            "options": ["approve_once", "reject", "cancel"],
        }
        await emit("approval_request", approval_payload)
        response = await wait_for_master(operation_id, "approval", approval_payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        if decision == "cancel":
            raise asyncio.CancelledError
        if decision != "approve_once":
            raise AgentRuntimeError(
                "Master rejected Cursor staged file changes; the Task workspace was not modified"
            )

        # The Master approved the frozen review bundle, not a live staging
        # directory. Detect orphan/helper writes during the approval wait and
        # refuse apply if either the staging tree or the reviewed bundle changed.
        if await asyncio.to_thread(self._workspace_snapshot, staging_dir) != reviewed_staging:
            raise AgentRuntimeError(
                "Cursor staging workspace changed after review; approved changes were not applied"
            )
        if await asyncio.to_thread(self._workspace_snapshot, review_dir) != reviewed_bundle:
            raise AgentRuntimeError(
                "Cursor staged review bundle changed after review; approved changes were not applied"
            )

        apply_task = asyncio.create_task(
            asyncio.to_thread(
                self._apply_staged_changes,
                request.working_dir,
                review_dir,
                baseline,
                changes,
            ),
            name=f"cursor-stage-apply-{request.agent_session_id}",
        )
        cancelled_during_apply = False
        try:
            # Cancelling the asyncio waiter cannot stop a worker thread that is
            # already writing user files. Shield it and keep the session-owned
            # staging/recovery resources alive until apply or rollback settles.
            await asyncio.shield(apply_task)
        except asyncio.CancelledError:
            cancelled_during_apply = True
            try:
                await apply_task
            except Exception:
                # A failed apply/rollback is more important than the cancel
                # request because it may require recovery action.
                raise
        await emit("file_change", {
            **file_payload,
            "phase": "completed",
            "status": "completed",
        })
        if cancelled_during_apply:
            raise asyncio.CancelledError

    def _read_only_staging_ignore_parts(self, working_dir: Path) -> frozenset[str]:
        ignored = set(CURSOR_READ_ONLY_IGNORE_NAMES)
        if working_dir.resolve() == self.root:
            ignored.update(CURSOR_NIRAI_REVIEW_IGNORE_NAMES)
        return frozenset(ignored)

    def _verify_read_only_review_unchanged(
        self,
        working_dir: Path,
        staging_dir: Path,
        baseline: dict[str, tuple[int, str]],
        *,
        ignore_parts: frozenset[str],
    ) -> None:
        staged = self._workspace_snapshot(staging_dir, ignore_parts=ignore_parts)
        changed_paths = self._changed_staged_paths(baseline, staged)
        if changed_paths:
            raise AgentRuntimeError(
                "Cursor read-only review attempted to modify its isolated staging copy; "
                "the review result was discarded"
            )
        current = self._workspace_snapshot(working_dir, ignore_parts=ignore_parts)
        if current != baseline:
            raise AgentRuntimeError(
                "Review target changed while Cursor was reviewing; rerun the review on the latest files"
            )

    def _prepare_staging_workspace(
        self,
        agent_session_id: str,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
        stable_key: str | None = None,
    ) -> tuple[Path, dict[str, tuple[int, str]]]:
        staging_root = self.workspace_policy.default_workspace_root
        staging_root.mkdir(parents=True, exist_ok=True)
        # Reservation begins before stale cleanup so a sibling start cannot
        # reap this session's staging directory while process creation is still
        # pending. The thread-safe ownership set is the cleanup authority;
        # `_preparing_ids` remains an event-loop diagnostic/state hint only.
        self._preparing_ids.add(agent_session_id)
        self._claim_runtime_id(agent_session_id)
        self._cleanup_stale_staging_workspaces(staging_root)
        if stable_key is not None:
            stable_digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
            staging_name = f".cursor-conversation-{stable_digest}"
        else:
            staging_name = f".cursor-stage-{agent_session_id}"
        staging_dir = (staging_root / staging_name).resolve()
        if (
            staging_dir.parent != staging_root
            or not staging_dir.name.startswith((".cursor-stage-", ".cursor-conversation-"))
        ):
            raise AgentSafetyError("Cursor staging workspace escaped Nirai internal staging root")
        self._cleanup_staging_workspace(staging_dir)
        # Snapshot validation also rejects symlink/junction entries. Avoid a
        # separate recursive link scan so large ignored trees are never walked
        # twice before staging.
        baseline = self._workspace_snapshot(working_dir, ignore_parts=ignore_parts)
        try:
            shutil.copytree(
                working_dir,
                staging_dir,
                copy_function=shutil.copy2,
                ignore=shutil.ignore_patterns(*sorted(ignore_parts)),
            )
        except OSError as exc:
            self._cleanup_staging_workspace(staging_dir)
            raise AgentRuntimeUnavailableError("Cursor staging workspace could not be prepared") from exc
        return staging_dir, baseline

    def _cleanup_stale_staging_workspaces(self, staging_root: Path) -> None:
        prefix = ".cursor-stage-"
        owned_ids = self._runtime_owned_snapshot()
        for child in staging_root.iterdir():
            if not child.name.startswith(prefix):
                continue
            agent_session_id = child.name[len(prefix):]
            if agent_session_id in owned_ids or not self._runtime_path_is_stale(child):
                continue
            self._cleanup_staging_workspace(child)

    @staticmethod
    def _runtime_path_is_stale(path: Path) -> bool:
        """Protect runtime state that may belong to another live Core process.

        In-process ownership is tracked explicitly, but another Core instance
        cannot share that set. A young directory is therefore never reaped as
        stale; abandoned state becomes eligible only after a conservative age.
        """
        try:
            return time.time() - path.stat().st_mtime >= CURSOR_STALE_RUNTIME_AGE_SEC
        except OSError:
            return False

    @classmethod
    def _iter_workspace_files(
        cls,
        root: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ):
        """Yield workspace files while pruning ignored directory trees early.

        ``Path.rglob`` still descends into ignored trees before callers can
        discard their results. For Nirai-root review that made ``runtime`` and
        ``node_modules`` expensive even though their contents were excluded.
        ``os.walk(topdown=True)`` lets us remove ignored directories before
        descent and validate every traversed symlink/junction in the same pass.
        """
        resolved_root = root.resolve()
        ignored = {part.casefold() for part in ignore_parts}

        def is_ignored(name: str) -> bool:
            folded = name.casefold()
            return any(fnmatch.fnmatchcase(folded, pattern) for pattern in ignored)
        for current_raw, dirnames, filenames in os.walk(
            resolved_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_raw)
            kept_dirs: list[str] = []
            for name in sorted(dirnames, key=str.casefold):
                if is_ignored(name):
                    continue
                child = current / name
                relative = child.relative_to(resolved_root)
                is_junction = getattr(child, "is_junction", lambda: False)
                if child.is_symlink() or is_junction():
                    raise AgentRuntimeError(
                        f"Cursor staging refuses linked workspace entries: {relative.as_posix()}"
                    )
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in sorted(filenames, key=str.casefold):
                if is_ignored(name):
                    continue
                path = current / name
                relative = path.relative_to(resolved_root)
                is_junction = getattr(path, "is_junction", lambda: False)
                if path.is_symlink() or is_junction():
                    raise AgentRuntimeError(
                        f"Cursor staging refuses linked workspace entries: {relative.as_posix()}"
                    )
                yield path

    @classmethod
    def _assert_workspace_has_no_links(
        cls,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ) -> None:
        # Iteration itself validates every traversed directory/file link.
        for _path in cls._iter_workspace_files(working_dir, ignore_parts=ignore_parts):
            pass

    @classmethod
    def _workspace_snapshot(
        cls,
        root: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ) -> dict[str, tuple[int, str]]:
        resolved_root = root.resolve()
        snapshot: dict[str, tuple[int, str]] = {}
        total_bytes = 0
        for path in cls._iter_workspace_files(resolved_root, ignore_parts=ignore_parts):
            if not path.is_file():
                continue
            relative = path.relative_to(resolved_root)
            size = path.stat().st_size
            total_bytes += size
            if len(snapshot) >= CURSOR_STAGE_FILE_LIMIT:
                raise AgentRuntimeError(
                    f"Cursor staging file limit exceeded ({CURSOR_STAGE_FILE_LIMIT})"
                )
            if total_bytes > CURSOR_STAGE_BYTE_LIMIT:
                raise AgentRuntimeError(
                    f"Cursor staging byte limit exceeded ({CURSOR_STAGE_BYTE_LIMIT})"
                )
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            snapshot[relative.as_posix()] = (size, digest.hexdigest())
        return snapshot

    def _collect_staged_changes(
        self,
        working_dir: Path,
        staging_dir: Path,
        baseline: dict[str, tuple[int, str]],
    ) -> list[dict[str, Any]]:
        staged = self._workspace_snapshot(staging_dir)
        changed_paths = self._changed_staged_paths(baseline, staged)
        self._validate_changed_staged_paths(changed_paths)
        return self._build_staged_change_manifest(
            working_dir,
            staging_dir,
            baseline,
            staged,
            changed_paths,
        )

    def _freeze_staged_changes(
        self,
        working_dir: Path,
        staging_dir: Path,
        review_dir: Path,
        baseline: dict[str, tuple[int, str]],
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[int, str]], dict[str, tuple[int, str]]]:
        staged_before = self._workspace_snapshot(staging_dir)
        changed_paths = self._changed_staged_paths(baseline, staged_before)
        self._validate_changed_staged_paths(changed_paths)
        if not changed_paths:
            return [], staged_before, {}

        self._cleanup_staging_workspace(review_dir)
        review_dir.mkdir(parents=True, exist_ok=False)
        try:
            for relative in changed_paths:
                if relative not in staged_before:
                    continue
                source = staging_dir / Path(relative)
                if not source.is_file():
                    raise AgentRuntimeError(
                        f"Cursor staged source disappeared during review freeze: {relative}"
                    )
                target = review_dir / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            staged_after = self._workspace_snapshot(staging_dir)
            if staged_after != staged_before:
                raise AgentRuntimeError(
                    "Cursor staging workspace changed while the review snapshot was being frozen"
                )
            reviewed_bundle = self._workspace_snapshot(review_dir)
            changes = self._build_staged_change_manifest(
                working_dir,
                review_dir,
                baseline,
                staged_before,
                changed_paths,
            )
            return changes, staged_before, reviewed_bundle
        except Exception:
            self._cleanup_staging_workspace(review_dir)
            raise

    @staticmethod
    def _changed_staged_paths(
        baseline: dict[str, tuple[int, str]],
        staged: dict[str, tuple[int, str]],
    ) -> list[str]:
        return sorted(
            {
                relative
                for relative in set(baseline) | set(staged)
                if baseline.get(relative) != staged.get(relative)
            },
            key=str.casefold,
        )

    @staticmethod
    def _validate_changed_staged_paths(changed_paths: list[str]) -> None:
        if "task.md" in changed_paths:
            raise AgentRuntimeError("Cursor attempted to modify protected Task metadata: task.md")
        if len(changed_paths) > 50:
            raise AgentRuntimeError(
                "Cursor produced more than 50 file changes; split the Task so every change can be reviewed safely"
            )

    @staticmethod
    def _build_staged_change_manifest(
        working_dir: Path,
        source_dir: Path,
        baseline: dict[str, tuple[int, str]],
        staged: dict[str, tuple[int, str]],
        changed_paths: list[str],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for relative in changed_paths:
            original = working_dir / Path(relative)
            staged_path = source_dir / Path(relative)
            if relative not in baseline:
                change_type = "create"
            elif relative not in staged:
                change_type = "delete"
            else:
                change_type = "modify"
            payload: dict[str, Any] = {
                "path": str(original),
                "relative_path": relative,
                "change_type": change_type,
            }
            diff = _cursor_file_diff(original, staged_path, relative, change_type)
            if diff is not None:
                payload["diff"] = diff
            changes.append(payload)
        return changes

    def _apply_staged_changes(
        self,
        working_dir: Path,
        staged_source_dir: Path,
        baseline: dict[str, tuple[int, str]],
        changes: list[dict[str, Any]],
    ) -> None:
        current = self._workspace_snapshot(working_dir)
        if current != baseline:
            raise AgentRuntimeError(
                "Task workspace changed while Cursor was working; staged changes were not applied"
            )

        recovery_root = (self.root / "runtime" / "cursor_recovery").resolve()
        try:
            recovery_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Recovery storage must exist before the first real workspace write.
            # If it cannot be prepared, fail without touching user files.
            raise AgentRuntimeError("Cursor recovery storage could not be prepared") from exc
        rollback_root = (recovery_root / f".RB-{uuid4()}").resolve()
        try:
            rollback_root.relative_to(recovery_root)
        except ValueError as exc:
            raise AgentRuntimeError("Cursor rollback backup path escaped recovery storage") from exc
        rollback_root.mkdir(parents=True, exist_ok=False)
        normalized: list[tuple[str, str, Path, Path | None]] = []
        preserve_rollback_root = False
        try:
            # Capture every original before the first real write. If any backup
            # fails, the Task workspace is still untouched.
            for change in changes:
                relative = change.get("relative_path")
                change_type = change.get("change_type")
                if not isinstance(relative, str) or change_type not in {"create", "modify", "delete"}:
                    raise AgentRuntimeError("Cursor staged change metadata is invalid")
                target = self.workspace_policy.assert_write_path(
                    Path(relative),
                    working_dir=working_dir,
                )
                backup: Path | None = None
                if change_type in {"modify", "delete"}:
                    if not target.is_file():
                        raise AgentRuntimeError(
                            f"Cursor staged target disappeared before apply: {relative}"
                        )
                    backup = rollback_root / Path(relative)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                normalized.append((relative, change_type, target, backup))

            try:
                for relative, change_type, target, _backup in normalized:
                    if change_type == "delete":
                        target.unlink()
                        continue
                    source = staged_source_dir / Path(relative)
                    if not source.is_file():
                        raise AgentRuntimeError(f"Cursor staged source is missing: {relative}")
                    target = self.workspace_policy.prepare_write_path(
                        target,
                        working_dir=working_dir,
                    )
                    self._atomic_copy_file(source, target)
            except Exception as apply_error:
                rollback_errors: list[str] = []
                for relative, change_type, target, backup in reversed(normalized):
                    try:
                        if change_type == "create":
                            target.unlink(missing_ok=True)
                        else:
                            if backup is None or not backup.is_file():
                                raise OSError("rollback backup is missing")
                            self._atomic_copy_file(backup, target)
                    except Exception as rollback_error:
                        rollback_errors.append(
                            f"{relative}: {type(rollback_error).__name__}: {_bounded_text(rollback_error, 200)}"
                        )
                if rollback_errors:
                    recovery_dir = recovery_root / f"REC-{uuid4()}"
                    try:
                        # Rollback data already lives outside the Cursor home.
                        # Publish it atomically only after the manifest is durable.
                        manifest = {
                            "working_dir": str(working_dir),
                            "staged_source_dir": str(staged_source_dir),
                            "rollback_errors": rollback_errors,
                            "apply_error": f"{type(apply_error).__name__}: {_bounded_text(apply_error, 500)}",
                        }
                        manifest_path = rollback_root / "recovery.json"
                        with manifest_path.open("w", encoding="utf-8", newline="\n") as handle:
                            handle.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(rollback_root, recovery_dir)
                    except OSError as recovery_error:
                        # The rollback tree is already outside normal Cursor-home
                        # cleanup. Leave it untouched for manual recovery.
                        preserve_rollback_root = True
                        raise AgentRuntimeError(
                            "Cursor staged apply failed, rollback was incomplete, and recovery backup "
                            f"could not be published; original backup remains at "
                            f"{rollback_root}: {_bounded_text(recovery_error, 300)}"
                        ) from apply_error
                    raise AgentRuntimeError(
                        "Cursor staged apply failed and rollback was incomplete; recovery backup preserved at "
                        f"{recovery_dir}: " + "; ".join(rollback_errors[:5])
                    ) from apply_error
                raise AgentRuntimeError(
                    f"Cursor staged apply failed and was rolled back: {_bounded_text(apply_error, 500)}"
                ) from apply_error
        finally:
            if not preserve_rollback_root:
                try:
                    self._cleanup_staging_workspace(rollback_root)
                except AgentRuntimeError:
                    LOGGER.warning("cursor_rollback_backup_cleanup_failed", exc_info=True)

    @staticmethod
    def _atomic_copy_file(source: Path, target: Path) -> None:
        if not target.parent.is_dir():
            raise AgentRuntimeError("Cursor apply target parent directory disappeared before write")
        temp = target.with_name(f".{target.name}.nirai-cursor-apply.tmp")
        try:
            shutil.copy2(source, temp)
            os.replace(temp, target)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                LOGGER.warning("cursor_apply_temp_cleanup_failed path=%s", temp, exc_info=True)

    def _cleanup_staging_workspace(self, staging_dir: Path) -> None:
        last_error: OSError | None = None
        for _ in range(CURSOR_STAGE_CLEANUP_RETRIES):
            try:
                shutil.rmtree(staging_dir, ignore_errors=False)
                if not staging_dir.exists():
                    return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
        if staging_dir.exists():
            raise AgentRuntimeError(
                f"Cursor staging workspace cleanup failed: {staging_dir.name}: {_bounded_text(last_error, 300)}"
            )

    def _prepare_cursor_home(
        self,
        agent_session_id: str,
        *,
        working_dir: Path | None = None,
        extra_denied_paths: tuple[Path, ...] = (),
        stable_key: str | None = None,
    ) -> Path:
        if stable_key is None:
            homes_root = self.root / "runtime" / "cursor_agent_homes"
            homes_root.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale_cursor_homes(homes_root)
            target = homes_root / agent_session_id
            if target.exists():
                self._cleanup_cursor_home(target)
        else:
            homes_root = self.root / "runtime" / "cursor_conversation_homes"
            homes_root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32]
            target = homes_root / f"CV-{digest}"
            target.mkdir(parents=True, exist_ok=True)
            # A previous crash may have left the injected login state behind.
            # Prefer losing provider-native continuity over retaining a copied
            # credential indefinitely.
            try:
                self._remove_cursor_auth_copy(target)
            except AgentRuntimeError:
                self._cleanup_cursor_home(target)
                target.mkdir(parents=True, exist_ok=True)
        config_dir = target / ".cursor"
        config_dir.mkdir(parents=True, exist_ok=True)
        source = _cursor_auth_state_source(self.root)
        if source is None:
            if stable_key is None:
                self._cleanup_cursor_home(target)
            else:
                self._cleanup_cursor_conversation_credentials(target)
            raise AgentRuntimeUnavailableError(
                "Cursor login state is unavailable. Sign in to Cursor Agent before using Cursor Agent Runtime."
            )
        auth_path = config_dir / "agent-cli-state.json"
        try:
            shutil.copyfile(source, auth_path)
            self._restrict_auth_permissions(auth_path)
        except (OSError, AgentRuntimeError) as exc:
            if stable_key is None:
                self._cleanup_cursor_home(target)
            else:
                try:
                    self._cleanup_cursor_conversation_credentials(target)
                except AgentRuntimeError:
                    self._cleanup_cursor_home(target)
            raise AgentRuntimeUnavailableError("Cursor authentication could not be isolated") from exc
        config = {
            "version": 1,
            "editor": {"vimMode": False},
            "permissions": {
                "allow": [],
                "deny": self._cursor_permission_denies(
                    working_dir,
                    extra_denied_paths=extra_denied_paths,
                ),
            },
            "approvalMode": "allowlist",
            "notifications": False,
            "hints": False,
            "rewind": False,
            "suggestNextPrompt": False,
            "display": {
                "showThinkingBlocks": False,
                "showStatusIndicators": False,
                "showStatusLineRunningTime": False,
            },
        }
        (config_dir / "cli-config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return target

    @staticmethod
    def _harden_cursor_cli_home(cursor_home: Path) -> None:
        config_path = cursor_home / ".cursor" / "cli-config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentRuntimeUnavailableError("Cursor CLI safety configuration could not be read") from exc
        permissions = config.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
            config["permissions"] = permissions
        deny = permissions.get("deny")
        if not isinstance(deny, list):
            deny = []
        for rule in (
            "Shell(*)",
            "WebSearch(*)",
            "Browser(*)",
            "Computer(*)",
        ):
            if rule not in deny:
                deny.append(rule)
        permissions["deny"] = deny
        permissions["allow"] = []
        config["approvalMode"] = "allowlist"
        try:
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AgentRuntimeUnavailableError("Cursor CLI safety configuration could not be written") from exc

    def _build_cursor_environment(self, cursor_home: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _ALLOWED_ENV_NAMES and isinstance(value, str)
        }
        temp = cursor_home / "Temp"
        temp.mkdir(parents=True, exist_ok=True)
        environment.update({
            "USERPROFILE": str(cursor_home),
            "HOME": str(cursor_home),
            "CURSOR_CONFIG_DIR": str(cursor_home / ".cursor"),
            # Cursor's cursor_login ACP authentication relies on the Windows
            # account's existing AppData-backed login path. Keep those two OS
            # locations for provider-internal auth only; CLI Read/Write denies
            # below block Agent tools from using them as data sources.
            "TEMP": str(temp),
            "TMP": str(temp),
        })
        return environment

    def _cursor_permission_denies(
        self,
        working_dir: Path | None,
        *,
        extra_denied_paths: tuple[Path, ...] = (),
    ) -> list[str]:
        denied_paths: set[Path] = set()
        actual_home = Path.home().resolve()
        denied_paths.add(actual_home)
        denied_paths.update(path.resolve() for path in extra_denied_paths)

        # Deny Nirai roots that ordinary task workers never need. The task's
        # own workspace is intentionally excluded.
        candidate_roots = [
            self.root / ".git",
            self.root / ".tools",
            self.root / "core",
            self.root / "world",
            self.root / "Docs",
            self.root / "residents",
            self.root / "avatars",
            self.root / "skills",
            self.root / "runtime" / "agent_sessions",
            self.root / "runtime" / "chat_sessions",
            self.root / "runtime" / "cursor_agent_homes",
            self.root / "runtime" / "cursor_conversation_homes",
            self.root / "runtime" / "cursor_profile",
            self.root / "runtime" / "world_memory",
        ]
        resolved_working = working_dir.resolve() if working_dir is not None else None
        for candidate in candidate_roots:
            resolved = candidate.resolve()
            if resolved_working is not None and (
                _path_is_within(resolved_working, resolved)
                or _path_is_within(resolved, resolved_working)
            ):
                continue
            denied_paths.add(resolved)

        # Existing sibling Task workspaces are also outside the current Task.
        if resolved_working is not None:
            workspace_root = (self.root / "runtime" / "workspace").resolve()
            if workspace_root.is_dir() and _path_is_within(resolved_working, workspace_root):
                for child in workspace_root.iterdir():
                    resolved = child.resolve()
                    if resolved == resolved_working or _path_is_within(resolved_working, resolved):
                        continue
                    denied_paths.add(resolved)

        deny = ["WebFetch(*)", "Mcp(*:*)", "Write(task.md)"]
        for path in sorted(denied_paths, key=lambda item: str(item).casefold()):
            pattern = _cursor_permission_path(path)
            deny.append(f"Read({pattern})")
            deny.append(f"Write({pattern})")
        return deny

    def _cleanup_stale_cursor_homes(self, homes_root: Path) -> None:
        owned_ids = self._runtime_owned_snapshot()
        for child in homes_root.iterdir():
            if child.name in owned_ids or not self._runtime_path_is_stale(child):
                continue
            self._cleanup_cursor_home(child)

    def discard_conversation_context(self, conversation_id: str) -> None:
        cleaned = conversation_id.strip()
        if not cleaned:
            return
        digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:32]
        target = self.root / "runtime" / "cursor_conversation_homes" / f"CV-{digest}"
        self._cleanup_cursor_home(target)

    @staticmethod
    def _remove_cursor_auth_copy(cursor_home: Path) -> None:
        auth_path = cursor_home / ".cursor" / "agent-cli-state.json"
        try:
            auth_path.unlink(missing_ok=True)
        except OSError as exc:
            raise AgentRuntimeError(
                f"Cursor conversation credential cleanup failed: {cursor_home.name}"
            ) from exc

    def _cleanup_cursor_conversation_credentials(self, cursor_home: Path) -> None:
        self._remove_cursor_auth_copy(cursor_home)
        for transient in (cursor_home / "Temp", cursor_home / ".nirai-staged-review"):
            try:
                shutil.rmtree(transient)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AgentRuntimeError(
                    f"Cursor conversation transient cleanup failed: {transient.name}"
                ) from exc

    def _cleanup_cursor_home_after_turn(self, cursor_home: Path, *, persistent: bool) -> None:
        if not persistent:
            self._cleanup_cursor_home(cursor_home)
            return
        try:
            self._cleanup_cursor_conversation_credentials(cursor_home)
        except AgentRuntimeError:
            # Credential isolation outranks provider-native continuity. If the
            # injected auth copy cannot be scrubbed, remove the whole provider
            # context rather than leave a credential-bearing conversation home.
            LOGGER.warning(
                "cursor_conversation_credential_scrub_failed home=%s removing_context=true",
                cursor_home.name,
                exc_info=True,
            )
            self._cleanup_cursor_home(cursor_home)

    @staticmethod
    def _restrict_auth_permissions(auth_path: Path) -> None:
        if os.name != "nt":
            return
        username = os.environ.get("USERNAME", "").strip()
        if not username:
            raise AgentRuntimeUnavailableError("Windows user is unavailable for Cursor auth ACL")
        domain = os.environ.get("USERDOMAIN", "").strip()
        principal = f"{domain}\\{username}" if domain else username
        result = subprocess.run(
            [
                "icacls.exe",
                str(auth_path),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(F)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            raise AgentRuntimeUnavailableError("Cursor Agent auth ACL could not be restricted")

    def _cleanup_cursor_home(self, cursor_home: Path) -> None:
        last_error: OSError | None = None
        for _ in range(CURSOR_HOME_CLEANUP_RETRIES):
            try:
                shutil.rmtree(cursor_home, ignore_errors=False)
                if not cursor_home.exists():
                    return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
        if cursor_home.exists():
            raise AgentRuntimeError(
                f"Cursor Agent credential home cleanup failed: {cursor_home.name}: {_bounded_text(last_error, 300)}"
            )

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


def _cursor_requires_exact_cli_model(model: str | None) -> bool:
    if not isinstance(model, str):
        return False
    cleaned = model.strip().casefold()
    return bool(cleaned) and "xhigh" in cleaned and "fast" not in cleaned


def _is_cursor_review_request(request: AgentRunRequest) -> bool:
    return request.purpose == "review" or (
        request.purpose == "work" and request.task_id.startswith("HR-")
    )


def _normalize_cursor_review_summary(summary: str) -> str:
    text = summary.strip()
    if not text:
        raise AgentRuntimeProtocolError("Cursor CLI review returned no verdict")
    needs_fix = re.search(r"(?<![A-Za-z])NEEDS\s+FIX(?![A-Za-z])", text, flags=re.IGNORECASE)
    safe = re.search(r"(?<![A-Za-z])SAFE(?![A-Za-z])", text, flags=re.IGNORECASE)
    if needs_fix is not None:
        verdict = "NEEDS FIX"
        match = needs_fix
    elif safe is not None:
        verdict = "SAFE"
        match = safe
    else:
        raise AgentRuntimeProtocolError("Cursor CLI review did not return SAFE or NEEDS FIX")
    remainder = (text[: match.start()] + text[match.end() :]).strip()
    return verdict if not remainder else f"{verdict}\n{remainder}"


def _contains_cursor_login(value: object) -> bool:
    return isinstance(value, list) and any(
        isinstance(item, dict) and item.get("id") == "cursor_login"
        for item in value
    )


def _config_by_category(options: list[object], category: str) -> dict[str, Any] | None:
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("category") == category or option.get("id") == category:
            if isinstance(option.get("id"), str):
                return option
    return None


def _select_entries(config: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    options = config.get("options")
    if not isinstance(options, list):
        return result
    for option in options:
        if not isinstance(option, dict):
            continue
        nested = option.get("options")
        if isinstance(nested, list):
            result.extend(item for item in nested if isinstance(item, dict))
        elif isinstance(option.get("value"), str):
            result.append(option)
    return result


def _resolve_select_value(config: dict[str, Any], requested: str) -> str | None:
    cleaned = requested.strip()
    requested_folded = cleaned.casefold()
    for option in _select_entries(config):
        value = option.get("value")
        name = option.get("name")
        if isinstance(value, str) and value.casefold() == requested_folded:
            return value
        if isinstance(name, str) and name.casefold() == requested_folded:
            return value if isinstance(value, str) else None
        if isinstance(value, str) and requested_folded in _cursor_cli_ids_for_acp_option(option):
            return value
    return None


def _cursor_cli_ids_for_acp_option(option: dict[str, Any]) -> set[str]:
    value = option.get("value")
    name = option.get("name")
    if not isinstance(value, str) or not isinstance(name, str):
        return set()
    if value == "default[]" or name.casefold() == "auto":
        return {"auto"}

    params = _cursor_model_params(value)
    base = name.casefold()
    cli_base = f"cursor-{base}" if base.startswith("grok-") else base
    thinking = params.get("thinking") == "true"
    level = (
        params.get("effort")
        or params.get("reasoning")
        or params.get("reasoning_effort")
    )
    fast = params.get("fast")

    prefix = cli_base + ("-thinking" if thinking else "")
    ids: set[str] = set()
    suffix_level = level
    if suffix_level == "extra-high":
        suffix_level = "xhigh"
    if suffix_level:
        ids.add(f"{prefix}-{suffix_level}" + ("-fast" if fast == "true" else ""))
        if suffix_level == "medium":
            ids.add(prefix + ("-fast" if fast == "true" else ""))
    else:
        ids.add(prefix + ("-fast" if fast == "true" else ""))
    return {item.casefold() for item in ids}


def _cursor_model_params(value: str) -> dict[str, str]:
    if "[" not in value or not value.endswith("]"):
        return {}
    raw = value.split("[", 1)[1][:-1]
    result: dict[str, str] = {}
    for part in raw.split(","):
        if "=" not in part:
            continue
        key, item = part.split("=", 1)
        key = key.strip().casefold()
        item = item.strip().casefold()
        if key:
            result[key] = item
    return result


def _provider_option_id(option: object) -> str | None:
    if isinstance(option, str):
        return option
    if not isinstance(option, dict):
        return None
    for key in ("optionId", "id", "value"):
        value = option.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _provider_option_kind(option: object) -> str | None:
    raw_kind = option.get("kind") if isinstance(option, dict) else None
    candidates = [raw_kind, _provider_option_id(option)]
    aliases = {
        "allow_once": "allow_once",
        "allow-once": "allow_once",
        "allow_always": "allow_always",
        "allow-always": "allow_always",
        "reject_once": "reject_once",
        "reject-once": "reject_once",
    }
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        normalized = candidate.strip().casefold()
        canonical = aliases.get(normalized)
        if canonical is not None:
            return canonical
    return None


def _permission_option_by_kind(provider_options: list[object], desired_kind: str) -> str | None:
    for option in provider_options:
        if _provider_option_kind(option) != desired_kind:
            continue
        option_id = _provider_option_id(option)
        if option_id is not None:
            return option_id
    return None


def _common_permission_options(provider_options: list[object]) -> list[str]:
    kinds = {_provider_option_kind(option) for option in provider_options}
    result: list[str] = []
    if "allow_once" in kinds:
        result.append("approve_once")
    if "allow_always" in kinds:
        result.append("approve_session")
    if "reject_once" in kinds:
        result.append("reject")
    result.append("cancel")
    return result


def _permission_option_for_decision(provider_options: list[object], decision: object) -> str | None:
    desired_kind = {
        "approve_once": "allow_once",
        "approve_session": "allow_always",
        "reject": "reject_once",
        "cancel": "reject_once",
    }.get(decision)
    if desired_kind is None:
        return None
    return _permission_option_by_kind(provider_options, desired_kind)


def _permission_reject_option(provider_options: list[object]) -> str | None:
    # Fail closed. Never substitute an arbitrary remaining option for reject;
    # ACP option ids are provider-defined and the semantic kind is authoritative.
    return _permission_option_by_kind(provider_options, "reject_once")


def _permission_reject_result(provider_options: object) -> dict[str, Any]:
    options = provider_options if isinstance(provider_options, list) else []
    option_id = _permission_reject_option(options)
    if option_id is None:
        return {"outcome": {"outcome": "cancelled"}}
    return {"outcome": {"outcome": "selected", "optionId": option_id}}


def _common_permission_kind(tool_kind: str) -> str:
    lowered = tool_kind.casefold()
    if lowered in {"edit", "write", "delete", "move"}:
        return "file_change"
    if lowered in {"execute", "shell", "terminal"}:
        return "command"
    return lowered or "tool"


def _is_external_tool(tool_kind: str, title: str) -> bool:
    lowered = tool_kind.casefold()
    title_folded = title.casefold()
    return (
        lowered in CURSOR_EXTERNAL_TOOL_KINDS
        or "web search" in title_folded
        or "web fetch" in title_folded
        or "mcp" in title_folded
    )


def _cursor_auth_state_source(root: Path) -> Path | None:
    candidates = [
        root / "runtime" / "cursor_profile" / ".cursor" / "agent-cli-state.json",
        Path.home() / ".cursor" / "agent-cli-state.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


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


def _path_is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _cursor_permission_path(path: Path) -> str:
    # Cursor CLI permission globs use forward slashes on all platforms.
    return path.resolve().as_posix().rstrip("/") + "/**"


def _cursor_review_manifest(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def payload_size(items: list[dict[str, Any]]) -> int:
        return len(json.dumps({"changes": items}, ensure_ascii=False, separators=(",", ":")))

    # Leave ample room below AgentRuntimeManager's 32k per-event budget for the
    # event envelope, operation id, and state metadata. Every changed path must
    # remain visible in the approval context. Diffs are optional; hidden paths
    # are not.
    review = [dict(change) for change in changes]
    if payload_size(review) <= 24_000:
        return review
    review = [
        {key: value for key, value in change.items() if key != "diff"}
        for change in changes
    ]
    if payload_size(review) <= 24_000:
        return review
    raise AgentRuntimeError(
        "Cursor staged change manifest is too large to review safely in one approval; split the Task"
    )


def _cursor_file_diff(
    original: Path,
    staged: Path,
    relative: str,
    change_type: str,
) -> str | None:
    old_text = _read_cursor_diff_text(original) if change_type != "create" else ""
    new_text = _read_cursor_diff_text(staged) if change_type != "delete" else ""
    if old_text is None or new_text is None:
        return None
    diff = "\n".join(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="",
    ))
    if len(diff) > 12_000:
        return diff[:11_999].rstrip() + "…"
    return diff


def _read_cursor_diff_text(path: Path) -> str | None:
    if not path.is_file():
        return ""
    try:
        if path.stat().st_size > CURSOR_DIFF_TEXT_FILE_LIMIT:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"
