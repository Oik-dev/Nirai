from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Awaitable, Callable

from core.brains.base import BrainUnavailableError
from core.brains.codex import resolve_codex_command

from .base import (
    AgentRunRequest,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    EmitEvent,
    WaitForMaster,
)
from .codex_events import normalize_codex_notification
from .safety import AgentSafetyError, AgentWorkspacePolicy


LOGGER = logging.getLogger("nirai.core.agent.codex")
_PROCESS_TREE_STOP_TIMEOUT_SEC = 8.0
_PROCESS_WAIT_STEP_SEC = 2.0
_CLIENT_TASK_FINISH_TIMEOUT_SEC = 1.0
_STDERR_READ_CHUNK_BYTES = 512
_STDERR_LINE_BUFFER_BYTES = 2048
_DIAGNOSTIC_EXCERPT_CHARS = 500


class _RpcError(RuntimeError):
    pass


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
                        future.set_exception(_RpcError(_rpc_error_message(error)))
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


class CodexAppServerAdapter:
    provider = "codex"

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
        self._active: dict[str, _ActiveCodexRun] = {}
        self._active_lock = asyncio.Lock()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        command = self._resolve_command()
        completion: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        final_messages: list[str] = []
        active: _ActiveCodexRun

        async def handle_notification(method: str, params: dict[str, Any]) -> None:
            for event_type, payload in normalize_codex_notification(
                method,
                params,
                working_dir=request.working_dir,
                workspace_policy=self.workspace_policy,
            ):
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
                    completion.set_result(turn)

        async def handle_server_request(
            provider_request_id: object,
            method: str,
            params: dict[str, Any],
        ) -> dict[str, Any]:
            request_key = _provider_request_key(provider_request_id)
            if method in self._APPROVAL_METHODS:
                if method == "item/fileChange/requestApproval":
                    try:
                        _validate_file_change_approval(
                            params,
                            workspace_policy=self.workspace_policy,
                            working_dir=request.working_dir,
                        )
                    except AgentSafetyError as exc:
                        await emit("error", {
                            "message": str(exc),
                            "code": "file_change_approval_rejected",
                            "recoverable": False,
                        })
                        return {"decision": "decline"}

                approval_payload = _common_approval_payload(request_key, method, params)
                await emit("approval_request", approval_payload)
                answer = await wait_for_master(request_key, "approval", approval_payload)
                raw_decision = answer.get("decision")
                decision = self._DECISIONS.get(raw_decision) if isinstance(raw_decision, str) else None
                if decision is None:
                    decision = "decline"
                return {"decision": decision}

            if method == self._QUESTION_METHOD:
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

        isolated_codex_home = self._prepare_isolated_codex_home(request.agent_session_id)
        child_env = self._build_child_env(isolated_codex_home)
        try:
            process = await self._spawn(command, request.working_dir, env=child_env)
        except Exception:
            self._remove_isolated_home(isolated_codex_home)
            raise
        client = _JsonLineAppServer(
            process,
            server_request_handler=handle_server_request,
            notification_handler=handle_notification,
        )
        active = _ActiveCodexRun(client=client)
        async with self._active_lock:
            self._active[request.agent_session_id] = active

        try:
            await client.request("initialize", {
                "clientInfo": {
                    "name": "nirai",
                    "title": "Nirai Agent Runtime",
                    "version": "0.1.0",
                },
                "capabilities": {"experimentalApi": False},
            })
            await client.notify("initialized")

            boundary_instruction = (
                "Nirai Agent Runtime boundary: only read and write files inside the current working directory. "
                "Do not read user-home files, sibling repositories, credentials, environment secrets, global skills, "
                "or configuration outside the working directory. If outside data is required, ask Master first."
            )
            thread_params: dict[str, Any] = {
                "cwd": str(request.working_dir),
                "approvalPolicy": "untrusted",
                "approvalsReviewer": "user",
                "sandbox": "workspace-write",
                "developerInstructions": boundary_instruction,
            }
            if request.model:
                thread_params["model"] = request.model
            thread_result = await client.request("thread/start", thread_params)
            thread = thread_result.get("thread")
            if not isinstance(thread, dict) or not isinstance(thread.get("id"), str):
                raise AgentRuntimeProtocolError("Codex thread/start did not return thread.id")
            active.thread_id = thread["id"]
            await emit("status_message", {"message": "Codex thread started"})

            turn_params: dict[str, Any] = {
                "threadId": active.thread_id,
                "input": [{"type": "text", "text": request.prompt}],
                "cwd": str(request.working_dir),
                "approvalPolicy": "untrusted",
                "approvalsReviewer": "user",
                "sandboxPolicy": {
                    "type": "workspaceWrite",
                    "writableRoots": [str(request.working_dir)],
                    "networkAccess": False,
                },
            }
            if request.model:
                turn_params["model"] = request.model
            if request.reasoning_effort:
                turn_params["effort"] = request.reasoning_effort
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
                message = _turn_error_message(error)
                raise AgentRuntimeProtocolError(message)
            if status == "interrupted":
                await emit("run_state", {"state": "cancelled"})
                return final_messages[-1] if final_messages else None
            if status != "completed":
                raise AgentRuntimeProtocolError(f"Codex turn ended in unexpected state: {status!r}")
            return final_messages[-1] if final_messages else None
        except _RpcError as exc:
            raise AgentRuntimeProtocolError(str(exc)) from exc
        finally:
            async with self._active_lock:
                self._active.pop(request.agent_session_id, None)
            await self._finalize_run_resources(client, isolated_codex_home)

    async def _finalize_run_resources(
        self,
        client: _JsonLineAppServer,
        isolated_codex_home: Path,
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
            self._remove_isolated_home(isolated_codex_home)
        except Exception as exc:
            cleanup_error = exc
            LOGGER.error(
                "codex_agent_home_cleanup_failed home=%s",
                isolated_codex_home.name,
                exc_info=True,
            )

        if cleanup_error is not None:
            raise AgentRuntimeUnavailableError(
                f"Codex Agent credential home cleanup failed: {isolated_codex_home.name}"
            ) from cleanup_error
        if close_error is not None:
            if isinstance(close_error, asyncio.CancelledError):
                raise close_error
            raise AgentRuntimeUnavailableError("Codex app-server shutdown failed") from close_error

    async def cancel(self, agent_session_id: str) -> bool:
        async with self._active_lock:
            active = self._active.get(agent_session_id)
            if active is None or active.thread_id is None or active.turn_id is None:
                return False
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

    def _prepare_isolated_codex_home(self, agent_session_id: str) -> Path:
        self._cleanup_stale_agent_homes()
        source_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()
        source_auth = source_home / "auth.json"
        if not source_auth.is_file():
            raise AgentRuntimeUnavailableError("Codex authentication is unavailable")

        isolated_root = (self.workspace_policy.root / "runtime" / "codex_agent_homes").resolve()
        isolated_home = (isolated_root / agent_session_id).resolve()
        try:
            isolated_home.relative_to(isolated_root)
        except ValueError as exc:
            raise AgentRuntimeUnavailableError("Codex Agent home path is invalid") from exc

        self._remove_isolated_home(isolated_home)
        isolated_home.mkdir(parents=True, exist_ok=False)
        auth_path = isolated_home / "auth.json"
        try:
            shutil.copyfile(source_auth, auth_path)
            self._restrict_auth_permissions(auth_path)
        except (OSError, AgentRuntimeUnavailableError) as exc:
            self._remove_isolated_home(isolated_home)
            raise AgentRuntimeUnavailableError("Codex authentication could not be isolated") from exc
        return isolated_home

    def _cleanup_stale_agent_homes(self) -> None:
        isolated_root = (self.workspace_policy.root / "runtime" / "codex_agent_homes").resolve()
        if isolated_root.is_dir():
            active_ids = set(self._active)
            for child in isolated_root.iterdir():
                if child.name in active_ids:
                    continue
                self._remove_isolated_home(child)

        # A pre-M4 implementation placed a complete Codex Home inside the
        # Agent workspace. It must never survive into a new Agent run.
        legacy_home = (
            self.workspace_policy.root
            / "runtime"
            / "workspace"
            / "m4-codex-agent-home"
        ).resolve()
        self._remove_isolated_home(legacy_home)

    @staticmethod
    def _remove_isolated_home(path: Path) -> None:
        if not path.exists():
            return
        last_error: OSError | None = None
        for _attempt in range(2):
            try:
                shutil.rmtree(path)
            except OSError as exc:
                last_error = exc
                continue
            if not path.exists():
                return
        if last_error is not None:
            raise AgentRuntimeUnavailableError(
                f"Codex Agent credential home could not be removed: {path.name}"
            ) from last_error
        raise AgentRuntimeUnavailableError(
            f"Codex Agent credential home still exists after cleanup: {path.name}"
        )

    @staticmethod
    def _restrict_auth_permissions(auth_path: Path) -> None:
        if os.name != "nt":
            return
        username = os.environ.get("USERNAME", "").strip()
        if not username:
            raise AgentRuntimeUnavailableError("Windows user is unavailable for Codex auth ACL")
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
            raise AgentRuntimeUnavailableError("Codex Agent auth ACL could not be restricted")

    @staticmethod
    def _build_child_env(isolated_codex_home: Path) -> dict[str, str]:
        allowed_names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_IDENTIFIER",
            "OS",
        }
        child_env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in allowed_names
        }
        isolated = str(isolated_codex_home)
        child_env["CODEX_HOME"] = isolated
        child_env["USERPROFILE"] = isolated
        child_env["HOME"] = isolated
        child_env["PYTHONIOENCODING"] = "utf-8"
        return child_env

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
