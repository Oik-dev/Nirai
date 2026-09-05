from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import difflib
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..brains.base import BrainError, BrainResponseError, BrainUnavailableError
from ..brains.gemini import (
    GEMINI_CANCEL_TIMEOUT_SEC,
    GEMINI_POLL_INTERVAL_SEC,
    _extract_interaction_text,
    _interaction_error,
    _request_json_async,
    is_antigravity_model,
    load_gemini_api_key,
)
from .base import (
    AgentRunRequest,
    AgentRuntimeError,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    EmitEvent,
    WaitForMaster,
)
from .safety import AgentSafetyError, AgentWorkspacePolicy


LOGGER = logging.getLogger("nirai.core.agent.antigravity")
ANTIGRAVITY_HTTP_POLL_LIMIT_SEC = 60.0 * 60.0
ANTIGRAVITY_FILE_TEXT_LIMIT = 512 * 1024
ANTIGRAVITY_READ_OUTPUT_LIMIT = 24_000
ANTIGRAVITY_COMMAND_OUTPUT_LIMIT = 24_000
ANTIGRAVITY_DIFF_LIMIT = 24_000
ANTIGRAVITY_LIST_ENTRY_LIMIT = 500
ANTIGRAVITY_LIST_OUTPUT_LIMIT = 24_000
ANTIGRAVITY_MAX_FUNCTION_CALLS_PER_TURN = 32
ANTIGRAVITY_MAX_FUNCTION_CALLS_PER_SESSION = 128
ANTIGRAVITY_MAX_INTERACTIONS_PER_SESSION = 128
ANTIGRAVITY_MAX_INCOMPLETE_CONTINUATIONS = 8
ANTIGRAVITY_ENVIRONMENT_RECOVERY_LIMIT_SEC = 8.0
ANTIGRAVITY_AGENT_DEFAULT = "antigravity-preview-05-2026"

@dataclass
class _ActiveAntigravity:
    interaction_id: str | None = None
    environment_id: str | None = None
    interaction_ids: set[str] = field(default_factory=set)
    environment_marker: str | None = None
    environment_baseline_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class _FileFingerprint:
    exists: bool
    digest: str | None
    size: int


class AntigravityAgentAdapter:
    """Gemini Antigravity managed-agent adapter with a Nirai-owned local bridge.

    The Google environment is deliberately not treated as the Task workspace.
    Local reads/writes/commands are exposed only as stateful custom functions,
    so every local side effect stays behind Nirai's workspace and Master gates.
    """

    provider = "gemini"
    capabilities = frozenset({"approval", "question", "plan", "file_diff", "command_result"})

    def __init__(self, workspace_policy: AgentWorkspacePolicy) -> None:
        self.workspace_policy = workspace_policy
        self.root = workspace_policy.root
        self.api_key = load_gemini_api_key(self.root)
        self._active: dict[str, _ActiveAntigravity] = {}
        self._active_lock = asyncio.Lock()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        if self.api_key is None:
            raise AgentRuntimeUnavailableError("GEMINI_API_KEY was not found in world/.env")
        model = request.model.strip() if isinstance(request.model, str) and request.model.strip() else ""
        if not model or not is_antigravity_model(model):
            raise AgentRuntimeProtocolError(
                "Gemini Agent Runtime requires an antigravity-* Resident model; "
                "ordinary Gemini conversation models are not Agent workers"
            )
        if request.reasoning_effort not in {None, ""}:
            raise AgentRuntimeProtocolError(
                "Antigravity Agent Runtime does not expose Nirai reasoning_effort; leave it unset"
            )

        working_dir = request.working_dir.resolve()
        self.workspace_policy.resolve_working_dir(str(working_dir), task_id=request.task_id)
        active = _ActiveAntigravity()
        async with self._active_lock:
            self._active[request.agent_session_id] = active

        handled_calls: set[str] = set()
        emitted_remote_command_steps: set[str] = set()
        remote_command_metadata: dict[str, tuple[str, str | None]] = {}
        announced_run_states: set[str] = set()
        incomplete_continuations = 0
        cleanup_errors: list[str] = []
        try:
            environment_id = await self._create_remote_environment(request, active)
            response = await self._create_initial_interaction(request, model, environment_id)
            _track_remote_response(active, response)
            while True:
                response = await self._poll_until_action_or_terminal(
                    request.agent_session_id,
                    response,
                    emit=emit,
                    emitted_remote_command_steps=emitted_remote_command_steps,
                    remote_command_metadata=remote_command_metadata,
                    announced_run_states=announced_run_states,
                )
                interaction_id = _required_id(response, "id", "Antigravity interaction")
                _track_remote_response(active, response)
                environment_id = response.get("environment_id")

                status = response.get("status")
                if status in {"completed", "requires_action", "incomplete"} and not active.environment_id:
                    raise AgentRuntimeProtocolError(
                        f"Antigravity {status} response did not contain environment_id; remote sandbox cleanup cannot be guaranteed"
                    )
                if status == "completed":
                    # Terminal interactions do not need a cancel call. Keep the
                    # environment id for explicit sandbox deletion, but clear the
                    # turn id so normal completion cannot fail on a redundant
                    # remote cancel request.
                    active.interaction_id = None
                    final_text = _extract_final_text(response)
                    if final_text:
                        await emit("assistant_message", {
                            "phase": "completed",
                            "message_phase": "final_answer",
                            "text": final_text,
                        })
                    return final_text or "Antigravity Agent completed the task"
                if status == "incomplete":
                    if not active.environment_id:
                        raise AgentRuntimeProtocolError(
                            "Antigravity incomplete response did not contain environment_id"
                        )
                    incomplete_continuations += 1
                    if incomplete_continuations > ANTIGRAVITY_MAX_INCOMPLETE_CONTINUATIONS:
                        raise AgentRuntimeError(
                            "Antigravity remained incomplete after 8 continuation turns"
                        )
                    response = await self._continue_incomplete_interaction(
                        request,
                        model,
                        interaction_id,
                        active.environment_id,
                    )
                    _track_remote_response(active, response)
                    continue
                if status != "requires_action":
                    if status in {"failed", "cancelled"}:
                        active.interaction_id = None
                    raise AgentRuntimeError(
                        f"Antigravity interaction failed: {_interaction_error(response)}"
                    )
                if not active.environment_id:
                    raise AgentRuntimeProtocolError(
                        "Antigravity requires_action response did not contain environment_id"
                    )

                calls = _pending_function_calls(response, handled_calls)
                if not calls:
                    raise AgentRuntimeProtocolError(
                        "Antigravity requires_action contained no new Nirai function call"
                    )
                if len(calls) > ANTIGRAVITY_MAX_FUNCTION_CALLS_PER_TURN:
                    raise AgentRuntimeProtocolError(
                        "Antigravity returned more than 32 pending Nirai function calls in one turn"
                    )
                if len(handled_calls) + len(calls) > ANTIGRAVITY_MAX_FUNCTION_CALLS_PER_SESSION:
                    raise AgentRuntimeError(
                        "Antigravity exceeded the 128 local function-call Session limit"
                    )
                function_results: list[dict[str, Any]] = []
                for call in calls:
                    call_id = _required_id(call, "id", "Antigravity function call")
                    handled_calls.add(call_id)
                    function_results.append(
                        await self._execute_function_call(
                            request,
                            call,
                            active,
                            emit=emit,
                            wait_for_master=wait_for_master,
                        )
                    )

                response = await self._continue_interaction(
                    request,
                    model,
                    interaction_id,
                    active.environment_id,
                    function_results,
                )
                _track_remote_response(active, response)
        except asyncio.CancelledError:
            raise
        except BrainUnavailableError as exc:
            raise AgentRuntimeUnavailableError(str(exc)) from exc
        except BrainResponseError as exc:
            raise AgentRuntimeProtocolError(str(exc)) from exc
        except BrainError as exc:
            raise AgentRuntimeError(str(exc)) from exc
        finally:
            async with self._active_lock:
                if self._active.get(request.agent_session_id) is active:
                    self._active.pop(request.agent_session_id, None)
            if active.environment_id is None and active.environment_marker:
                try:
                    active.environment_id = await self._recover_remote_environment(
                        active.environment_marker,
                        active.environment_baseline_ids,
                    )
                except AgentRuntimeError as exc:
                    cleanup_errors.append(str(exc))
            if active.interaction_id:
                try:
                    await self._cancel_remote_interaction(active.interaction_id, ignore_terminal=True)
                except AgentRuntimeError as exc:
                    cleanup_errors.append(str(exc))
            if active.environment_id:
                try:
                    await self._delete_environment(active.environment_id)
                except AgentRuntimeError as exc:
                    cleanup_errors.append(str(exc))
            if active.interaction_ids:
                delete_results = await asyncio.gather(
                    *(self._delete_interaction(interaction_id) for interaction_id in sorted(active.interaction_ids)),
                    return_exceptions=True,
                )
                for result in delete_results:
                    if isinstance(result, BaseException):
                        cleanup_errors.append(_bounded_text(result, 500))
            if cleanup_errors:
                raise AgentRuntimeError("; ".join(cleanup_errors))

    async def cancel(self, agent_session_id: str) -> bool:
        async with self._active_lock:
            active = self._active.get(agent_session_id)
        if active is None:
            return False
        attempted = False
        if active.interaction_id:
            attempted = True
            try:
                await self._cancel_remote_interaction(active.interaction_id, ignore_terminal=True)
            except AgentRuntimeError:
                LOGGER.warning(
                    "antigravity_cancel_remote_failed agent_session_id=%s interaction_id=%s",
                    agent_session_id,
                    active.interaction_id,
                    exc_info=True,
                )
        return attempted

    async def _create_remote_environment(
        self,
        request: AgentRunRequest,
        active: _ActiveAntigravity,
    ) -> str:
        assert self.api_key is not None
        # List before create so a lost CreateEnvironment response can be
        # reconciled against only newly-created environments. A unique inline
        # marker makes that reconciliation safe even if another client creates
        # an environment concurrently in the same Google project.
        active.environment_baseline_ids = await self._list_remote_environment_ids()
        marker = f"nirai:{request.agent_session_id}:{uuid4().hex}"
        active.environment_marker = marker
        payload = {
            "network": "disabled",
            "sources": [{
                "type": "inline",
                "content": marker,
                "target": "nirai-session-owner.txt",
            }],
        }
        try:
            response = await _request_json_async(self.api_key, "/environments", payload)
            environment_id = _environment_resource_id(response, "Antigravity environment")
        except (BrainError, AgentRuntimeProtocolError):
            recovered = await self._recover_remote_environment(
                marker,
                active.environment_baseline_ids,
            )
            if recovered is None:
                raise
            environment_id = recovered
        active.environment_id = environment_id
        return environment_id

    async def _list_remote_environment_ids(self) -> set[str]:
        assert self.api_key is not None
        result: set[str] = set()
        page_token: str | None = None
        for _ in range(20):
            path = "/environments?page_size=1000"
            if page_token:
                path += f"&page_token={quote(page_token, safe='')}"
            response = await _request_json_async(self.api_key, path, method="GET")
            environments = response.get("environments")
            if environments is not None and not isinstance(environments, list):
                raise AgentRuntimeProtocolError("Antigravity environment list was malformed")
            for environment in environments or []:
                if not isinstance(environment, dict):
                    continue
                environment_id = _optional_environment_resource_id(environment)
                if environment_id is not None:
                    result.add(environment_id)
            next_page_token = response.get("next_page_token")
            if not isinstance(next_page_token, str) or not next_page_token:
                return result
            page_token = next_page_token
        raise AgentRuntimeError("Antigravity environment list exceeded the 20-page recovery limit")

    async def _recover_remote_environment(
        self,
        marker: str,
        baseline_ids: set[str],
    ) -> str | None:
        assert self.api_key is not None
        started = asyncio.get_running_loop().time()
        last_error: BrainError | None = None
        while asyncio.get_running_loop().time() - started <= ANTIGRAVITY_ENVIRONMENT_RECOVERY_LIMIT_SEC:
            try:
                current_ids = await self._list_remote_environment_ids()
                for environment_id in sorted(current_ids - baseline_ids):
                    environment = await _request_json_async(
                        self.api_key,
                        f"/environments/{quote(environment_id, safe='')}",
                        method="GET",
                    )
                    sources = environment.get("sources")
                    if not isinstance(sources, list):
                        continue
                    if any(
                        isinstance(source, dict)
                        and source.get("type") == "inline"
                        and source.get("content") == marker
                        and source.get("target") == "nirai-session-owner.txt"
                        for source in sources
                    ):
                        return environment_id
                last_error = None
            except BrainError as exc:
                last_error = exc
            await asyncio.sleep(0.25)
        if last_error is not None:
            raise AgentRuntimeError(
                f"Antigravity could not verify remote environment cleanup after a lost create response: {last_error}"
            ) from last_error
        return None

    async def _create_initial_interaction(
        self,
        request: AgentRunRequest,
        model: str,
        environment_id: str,
    ) -> dict[str, Any]:
        assert self.api_key is not None
        payload = {
            "agent": model,
            "input": self._agent_prompt(request),
            "system_instruction": self._system_instruction(request),
            "environment": environment_id,
            "background": True,
            "store": True,
            "tools": _antigravity_local_tools(),
        }
        return await _request_json_async(self.api_key, "/interactions", payload)

    async def _continue_interaction(
        self,
        request: AgentRunRequest,
        model: str,
        previous_interaction_id: str,
        environment_id: str,
        function_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        assert self.api_key is not None
        return await _request_json_async(
            self.api_key,
            "/interactions",
            {
                "agent": model,
                "previous_interaction_id": previous_interaction_id,
                "environment": environment_id,
                "input": function_results,
                "system_instruction": self._system_instruction(request),
                "tools": _antigravity_local_tools(),
                "background": True,
                "store": True,
            },
        )

    async def _continue_incomplete_interaction(
        self,
        request: AgentRunRequest,
        model: str,
        previous_interaction_id: str,
        environment_id: str,
    ) -> dict[str, Any]:
        assert self.api_key is not None
        return await _request_json_async(
            self.api_key,
            "/interactions",
            {
                "agent": model,
                "previous_interaction_id": previous_interaction_id,
                "environment": environment_id,
                "input": "Continue the Nirai Task from where you stopped. Preserve all prior safety boundaries.",
                "system_instruction": self._system_instruction(request),
                "tools": _antigravity_local_tools(),
                "background": True,
                "store": True,
            },
        )

    async def _poll_until_action_or_terminal(
        self,
        agent_session_id: str,
        response: dict[str, Any],
        *,
        emit: EmitEvent,
        emitted_remote_command_steps: set[str],
        remote_command_metadata: dict[str, tuple[str, str | None]],
        announced_run_states: set[str],
    ) -> dict[str, Any]:
        assert self.api_key is not None
        started = asyncio.get_running_loop().time()
        while True:
            interaction_id = _required_id(response, "id", "Antigravity interaction")
            environment_id = response.get("environment_id")
            async with self._active_lock:
                active = self._active.get(agent_session_id)
                if active is not None:
                    _track_remote_response(active, response)
            environment_value = environment_id if isinstance(environment_id, str) and environment_id else None
            run_state_marker = f"{interaction_id}:{environment_value or ''}"
            if run_state_marker not in announced_run_states:
                announced_run_states.add(run_state_marker)
                await emit("run_state", {
                    "state": "running",
                    **({"provider_session_id": environment_value} if environment_value else {}),
                    "provider_turn_id": interaction_id,
                })
            await _emit_remote_code_execution(
                response,
                emitted_remote_command_steps,
                remote_command_metadata,
                emit,
            )
            if response.get("status") not in {"queued", "in_progress"}:
                return response
            if asyncio.get_running_loop().time() - started > ANTIGRAVITY_HTTP_POLL_LIMIT_SEC:
                await self._cancel_remote_interaction(interaction_id, ignore_terminal=True)
                raise AgentRuntimeUnavailableError("Antigravity interaction polling exceeded 60 minutes")
            await asyncio.sleep(GEMINI_POLL_INTERVAL_SEC)
            response = await _request_json_async(
                self.api_key,
                f"/interactions/{quote(interaction_id, safe='')}",
                method="GET",
            )

    async def _cancel_remote_interaction(
        self,
        interaction_id: str,
        *,
        ignore_terminal: bool,
    ) -> None:
        assert self.api_key is not None
        try:
            await asyncio.wait_for(
                _request_json_async(
                    self.api_key,
                    f"/interactions/{quote(interaction_id, safe='')}/cancel",
                    method="POST",
                ),
                timeout=GEMINI_CANCEL_TIMEOUT_SEC,
            )
        except (asyncio.TimeoutError, BrainError) as exc:
            detail = str(exc)
            if ignore_terminal and any(marker in detail.casefold() for marker in (
                "already completed",
                "already cancelled",
                "cannot cancel",
                "failed_precondition",
                "not found",
                "404",
            )):
                return
            raise AgentRuntimeError(
                f"Antigravity remote interaction cancel failed: {detail[:500]}"
            ) from exc

    async def _delete_interaction(self, interaction_id: str) -> None:
        assert self.api_key is not None
        try:
            await _request_json_async(
                self.api_key,
                f"/interactions/{quote(interaction_id, safe='')}",
                method="DELETE",
            )
        except BrainError as exc:
            detail = str(exc)
            if "404" in detail or "not found" in detail.casefold():
                return
            raise AgentRuntimeError(
                f"Antigravity stored interaction cleanup failed: {detail[:500]}"
            ) from exc

    async def _delete_environment(self, environment_id: str) -> None:
        assert self.api_key is not None
        try:
            await _request_json_async(
                self.api_key,
                f"/environments/{quote(environment_id, safe='')}",
                method="DELETE",
            )
        except BrainError as exc:
            detail = str(exc)
            if "404" in detail or "not found" in detail.casefold():
                return
            raise AgentRuntimeError(
                f"Antigravity remote environment cleanup failed: {detail[:500]}"
            ) from exc

    async def _execute_function_call(
        self,
        request: AgentRunRequest,
        call: dict[str, Any],
        active: _ActiveAntigravity,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        call_id = _required_id(call, "id", "Antigravity function call")
        name = call.get("name")
        arguments = call.get("arguments")
        if not isinstance(name, str) or not name:
            return _function_result(call_id, "unknown", {"ok": False, "error": "missing function name"}, True)
        if not isinstance(arguments, dict):
            arguments = {}

        await emit("tool_call", {
            "operation_id": call_id,
            "tool_type": name,
            "phase": "started",
            "status": "running",
            **_tool_call_summary(name, arguments),
        })
        try:
            if name == "nirai_list_files":
                result = self._list_files(request, arguments)
            elif name == "nirai_read_text_file":
                result = self._read_text_file(request, arguments)
            elif name == "nirai_write_text_file":
                result = await self._write_text_file(
                    request,
                    call_id,
                    arguments,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            elif name == "nirai_edit_text_file":
                result = await self._edit_text_file(
                    request,
                    call_id,
                    arguments,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            elif name == "nirai_delete_file":
                result = await self._delete_file(
                    request,
                    call_id,
                    arguments,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            elif name == "nirai_ask_master":
                result = await self._ask_master(
                    call_id,
                    arguments,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            elif name == "nirai_submit_plan":
                result = await self._submit_plan(
                    call_id,
                    arguments,
                    emit=emit,
                    wait_for_master=wait_for_master,
                )
            else:
                raise AgentRuntimeProtocolError(f"Unknown Antigravity local function: {name}")
            await emit("tool_call", {
                "operation_id": call_id,
                "tool_type": name,
                "phase": "completed",
                "status": "completed",
            })
            return _function_result(call_id, name, result, False)
        except asyncio.CancelledError:
            raise
        except (AgentSafetyError, AgentRuntimeError, OSError, UnicodeError, ValueError) as exc:
            message = _bounded_text(exc, 1000)
            await emit("tool_call", {
                "operation_id": call_id,
                "tool_type": name,
                "phase": "completed",
                "status": "failed",
                "message": message,
            })
            return _function_result(call_id, name, {"ok": False, "error": message}, True)

    def _list_files(self, request: AgentRunRequest, arguments: dict[str, Any]) -> dict[str, Any]:
        directory = _resolve_relative_path(
            request.working_dir,
            arguments.get("path", "."),
            must_exist=True,
        )
        if not directory.is_dir():
            raise AgentRuntimeError("nirai_list_files target is not a directory")
        entries: list[dict[str, Any]] = []
        for child in sorted(directory.iterdir(), key=lambda value: value.name.casefold()):
            if len(entries) >= ANTIGRAVITY_LIST_ENTRY_LIMIT:
                break
            resolved = child.resolve()
            if not _is_within(resolved, request.working_dir.resolve()):
                entry: dict[str, Any] = {"name": child.name, "type": "blocked_link"}
            else:
                entry = {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    **({"size": child.stat().st_size} if child.is_file() else {}),
                }
            projected_chars = len(json.dumps(entries, ensure_ascii=False)) + len(json.dumps(entry, ensure_ascii=False))
            if projected_chars > ANTIGRAVITY_LIST_OUTPUT_LIMIT:
                break
            entries.append(entry)
        return {
            "ok": True,
            "path": _relative_display(directory, request.working_dir),
            "entries": entries,
            "truncated": len(entries) >= ANTIGRAVITY_LIST_ENTRY_LIMIT,
        }

    def _read_text_file(self, request: AgentRunRequest, arguments: dict[str, Any]) -> dict[str, Any]:
        target = _resolve_relative_path(request.working_dir, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_read_text_file target is not a file")
        if target.stat().st_size > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_read_text_file refuses files larger than 512 KiB")
        text = target.read_text(encoding="utf-8")
        start_line = _bounded_int(arguments.get("start_line"), default=1, minimum=1, maximum=1_000_000)
        max_lines = _bounded_int(arguments.get("max_lines"), default=200, minimum=1, maximum=500)
        lines = text.splitlines()
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        content = "\n".join(selected)
        if len(content) > ANTIGRAVITY_READ_OUTPUT_LIMIT:
            content = content[: ANTIGRAVITY_READ_OUTPUT_LIMIT - 1] + "…"
        return {
            "ok": True,
            "path": _relative_display(target, request.working_dir),
            "start_line": start_line,
            "content": content,
            "has_more": start_line - 1 + max_lines < len(lines),
        }

    async def _write_text_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise AgentRuntimeProtocolError("nirai_write_text_file content must be a string")
        if len(content.encode("utf-8")) > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_write_text_file refuses content larger than 512 KiB")
        before_text = _read_small_text(target)
        if target.exists() and before_text is None:
            raise AgentRuntimeError(
                "nirai_write_text_file refuses to replace an existing non-UTF-8 or >512 KiB file because Master cannot review the complete old text"
            )
        before = _fingerprint(target)
        diff = _review_diff(before_text or "", content, _relative_display(target, request.working_dir))
        await self._approve_file_change(
            request,
            call_id,
            target,
            "modify" if before.exists else "create",
            diff,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        target = self.workspace_policy.prepare_write_path(
            target,
            working_dir=request.working_dir,
        )
        _atomic_write_text(target, content)
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "completed",
            "changes": [{
                "relative_path": _relative_display(target, request.working_dir),
                "change_type": "modify" if before.exists else "create",
            }],
        })
        return {"ok": True, "path": _relative_display(target, request.working_dir)}

    async def _edit_text_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_edit_text_file target is not a file")
        before_text = _read_small_text(target)
        if before_text is None:
            raise AgentRuntimeError("nirai_edit_text_file requires a UTF-8 text file <= 512 KiB")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        replace_all = arguments.get("replace_all") is True
        if not isinstance(old_text, str) or not old_text:
            raise AgentRuntimeProtocolError("nirai_edit_text_file old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise AgentRuntimeProtocolError("nirai_edit_text_file new_text must be a string")
        count = before_text.count(old_text)
        if count == 0:
            raise AgentRuntimeError("nirai_edit_text_file old_text was not found")
        if not replace_all and count != 1:
            raise AgentRuntimeError(
                "nirai_edit_text_file old_text is ambiguous; provide more context or replace_all=true"
            )
        after_text = before_text.replace(old_text, new_text) if replace_all else before_text.replace(old_text, new_text, 1)
        if len(after_text.encode("utf-8")) > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_edit_text_file result would exceed 512 KiB")
        before = _fingerprint(target)
        diff = _review_diff(before_text, after_text, _relative_display(target, request.working_dir))
        await self._approve_file_change(
            request,
            call_id,
            target,
            "modify",
            diff,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        target = self.workspace_policy.prepare_write_path(
            target,
            working_dir=request.working_dir,
        )
        _atomic_write_text(target, after_text)
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "completed",
            "changes": [{"relative_path": _relative_display(target, request.working_dir), "change_type": "modify"}],
        })
        return {"ok": True, "path": _relative_display(target, request.working_dir), "replacements": count if replace_all else 1}

    async def _delete_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_delete_file only deletes files")
        before = _fingerprint(target)
        relative = _relative_display(target, request.working_dir)
        await self._approve_file_change(
            request,
            call_id,
            target,
            "delete",
            None,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
            description=f"Delete {relative} ({before.size} bytes, sha256={before.digest})",
        )
        target.unlink()
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "completed",
            "changes": [{"relative_path": relative, "change_type": "delete"}],
        })
        return {"ok": True, "path": relative}

    async def _approve_file_change(
        self,
        request: AgentRunRequest,
        call_id: str,
        target: Path,
        change_type: str,
        diff: str | None,
        baseline: _FileFingerprint,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
        description: str | None = None,
    ) -> None:
        relative = _relative_display(target, request.working_dir)
        approved_canonical_path = target.resolve()
        change: dict[str, Any] = {
            "path": str(target),
            "relative_path": relative,
            "change_type": change_type,
        }
        if diff:
            change["diff"] = diff
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "proposed",
            "status": "pending_approval",
            "changes": [change],
        })
        payload = {
            "request_id": call_id,
            "operation_id": call_id,
            "kind": "file_change",
            "title": f"Antigravity wants to {change_type} {relative}",
            "description": description or "Review the exact local Task workspace change",
            "grant_root": str(request.working_dir),
            "options": ["approve_once", "reject", "cancel"],
        }
        await emit("approval_request", payload)
        response = await wait_for_master(call_id, "approval", payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        if decision == "cancel":
            raise asyncio.CancelledError
        if decision != "approve_once":
            raise AgentRuntimeError("Master rejected the Antigravity local file change")
        current_canonical_path = target.resolve()
        if current_canonical_path != approved_canonical_path:
            raise AgentRuntimeError(
                "Task workspace path topology changed while Antigravity approval was pending; local change was not applied"
            )
        self.workspace_policy.assert_write_path(current_canonical_path, working_dir=request.working_dir)
        if _fingerprint(current_canonical_path) != baseline:
            raise AgentRuntimeError(
                "Task workspace file changed while Antigravity approval was pending; local change was not applied"
            )

    async def _ask_master(
        self,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        question = arguments.get("question")
        if not isinstance(question, str) or not question.strip():
            raise AgentRuntimeProtocolError("nirai_ask_master question must be a non-empty string")
        raw_options = arguments.get("options")
        options = [value.strip() for value in raw_options if isinstance(value, str) and value.strip()] if isinstance(raw_options, list) else []
        option_payload = [
            {"id": f"o{index + 1}", "label": label}
            for index, label in enumerate(options[:12])
        ]
        payload = {
            "request_id": call_id,
            "title": arguments.get("title") if isinstance(arguments.get("title"), str) else "Antigravity question",
            "questions": [{
                "id": "q1",
                "question": question.strip(),
                "options": option_payload,
                "allow_multiple": arguments.get("allow_multiple") is True,
                "allow_free_text": True,
            }],
        }
        await emit("question_request", payload)
        response = await wait_for_master(call_id, "question", payload)
        answers = response.get("answers") if isinstance(response, dict) else None
        values = answers.get("q1") if isinstance(answers, dict) else None
        selected = values if isinstance(values, list) else []
        labels_by_id = {item["id"]: item["label"] for item in option_payload}
        normalized: list[str] = []
        total_chars = 0
        for value in selected:
            if not isinstance(value, str) or not value.strip():
                continue
            answer = labels_by_id.get(value, value).strip()
            if not answer:
                continue
            answer = answer[:4_000]
            remaining = 12_000 - total_chars
            if remaining <= 0:
                break
            answer = answer[:remaining]
            normalized.append(answer)
            total_chars += len(answer)
        if not normalized:
            raise AgentRuntimeError("Master did not answer the Antigravity question")
        return {"ok": True, "answers": normalized}

    async def _submit_plan(
        self,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        markdown = arguments.get("plan")
        if not isinstance(markdown, str) or not markdown.strip():
            raise AgentRuntimeProtocolError("nirai_submit_plan plan must be a non-empty string")
        payload = {
            "request_id": call_id,
            "approval_required": True,
            "explanation": markdown.strip()[:24_000],
            "steps": [],
        }
        await emit("plan", payload)
        response = await wait_for_master(call_id, "plan", payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        reason = response.get("reason") if isinstance(response, dict) else None
        if decision == "approve":
            return {"ok": True, "decision": "approve"}
        if decision == "cancel":
            raise asyncio.CancelledError
        return {
            "ok": False,
            "decision": "revise",
            **({"reason": reason} if isinstance(reason, str) and reason.strip() else {}),
        }

    @staticmethod
    def _system_instruction(request: AgentRunRequest) -> str:
        return (
            "You are an Antigravity worker controlled by Nirai. The Google remote filesystem is scratch space only; "
            "it is NOT the user's local Task workspace and changes there do not complete the task. "
            "For every local project read, list, write, edit, delete, Master question, or plan approval, "
            "use the provided nirai_* custom functions. Run commands only with the remote code_execution tool; remote "
            "commands are sandbox-only and cannot directly change the local Task workspace. Never claim a local change "
            "succeeded unless the matching nirai_* function returned ok=true. Network access is disabled for this baseline. Do not request or expose "
            "secrets. Do not reveal private chain-of-thought. Keep the final answer concise and state what local files or "
            "commands actually succeeded. Never modify task.md."
        )

    @staticmethod
    def _agent_prompt(request: AgentRunRequest) -> str:
        return (
            f"Nirai Task {request.task_id} for Resident {request.resident}.\n"
            "The authoritative local working directory is exposed only through nirai_* functions.\n\n"
            f"Task:\n{request.prompt.strip()}"
        )


def _antigravity_local_tools() -> list[dict[str, Any]]:
    return [
        {"type": "code_execution"},
        {
            "type": "function",
            "name": "nirai_list_files",
            "description": "List one directory inside the authoritative local Nirai Task workspace. Paths must be relative.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Relative directory path, default ."}},
            },
        },
        {
            "type": "function",
            "name": "nirai_read_text_file",
            "description": "Read UTF-8 text from a file inside the authoritative local Nirai Task workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "max_lines": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "nirai_write_text_file",
            "description": "Create or replace one UTF-8 text file in the local Task workspace. Nirai asks Master before applying it.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
        {
            "type": "function",
            "name": "nirai_edit_text_file",
            "description": "Replace exact text in one UTF-8 local Task workspace file. Nirai asks Master before applying it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                    "replace_all": {"type": "boolean"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
        {
            "type": "function",
            "name": "nirai_delete_file",
            "description": "Delete one file in the local Task workspace after Master approval. Directories cannot be deleted.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
        {
            "type": "function",
            "name": "nirai_ask_master",
            "description": "Ask Master a question when task requirements are ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "allow_multiple": {"type": "boolean"},
                },
                "required": ["question"],
            },
        },
        {
            "type": "function",
            "name": "nirai_submit_plan",
            "description": "Submit a proposed implementation plan to Master for approve/revise/cancel before risky or broad work.",
            "parameters": {
                "type": "object",
                "properties": {"plan": {"type": "string"}},
                "required": ["plan"],
            },
        },
    ]


def _pending_function_calls(payload: dict[str, Any], handled: set[str]) -> list[dict[str, Any]]:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise AgentRuntimeProtocolError("Antigravity interaction contained no steps")
    completed_ids = {
        step.get("call_id")
        for step in steps
        if isinstance(step, dict) and step.get("type") == "function_result" and isinstance(step.get("call_id"), str)
    }
    result: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "function_call":
            continue
        call_id = step.get("id")
        if not isinstance(call_id, str) or not call_id or call_id in completed_ids or call_id in handled:
            continue
        result.append(step)
    return result


def _function_result(call_id: str, name: str, result: dict[str, Any], is_error: bool) -> dict[str, Any]:
    return {
        "type": "function_result",
        "name": name,
        "call_id": call_id,
        "result": result,
        **({"is_error": True} if is_error else {}),
    }


async def _emit_remote_code_execution(
    payload: dict[str, Any],
    emitted_steps: set[str],
    command_metadata: dict[str, tuple[str, str | None]],
    emit: EmitEvent,
) -> None:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        return
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "code_execution_call":
            continue
        call_id = step.get("id")
        arguments = step.get("arguments")
        if not isinstance(call_id, str) or not call_id or not isinstance(arguments, dict):
            continue
        code = arguments.get("code")
        language = arguments.get("language")
        if not isinstance(code, str) or not code.strip():
            continue
        command_metadata[call_id] = (
            code,
            language if isinstance(language, str) and language else None,
        )
        marker = f"call:{call_id}"
        if marker in emitted_steps:
            continue
        emitted_steps.add(marker)
        await emit("command_execution", {
            "operation_id": call_id,
            "phase": "running",
            "status": "running",
            "command": code[:12_000],
            "cwd": "Google Antigravity remote sandbox",
            "execution_scope": "remote_sandbox",
            **({"language": language} if isinstance(language, str) and language else {}),
        })

    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "code_execution_result":
            continue
        call_id = step.get("call_id")
        if not isinstance(call_id, str) or not call_id:
            continue
        marker = f"result:{call_id}"
        if marker in emitted_steps:
            continue
        emitted_steps.add(marker)
        result = step.get("result")
        output = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
        if len(output) > ANTIGRAVITY_COMMAND_OUTPUT_LIMIT:
            output = output[: ANTIGRAVITY_COMMAND_OUTPUT_LIMIT - 1].rstrip() + "…"
        code, language = command_metadata.get(call_id, ("", None))
        await emit("command_execution", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "failed" if step.get("is_error") is True else "completed",
            "command": code[:12_000],
            "cwd": "Google Antigravity remote sandbox",
            "execution_scope": "remote_sandbox",
            "output": output,
            **({"language": language} if language else {}),
        })


def _extract_final_text(payload: dict[str, Any]) -> str:
    try:
        return _extract_interaction_text(payload)
    except BrainResponseError:
        return ""


def _resolve_relative_path(working_dir: Path, raw_value: object, *, must_exist: bool) -> Path:
    if raw_value is None:
        raw_value = "."
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AgentSafetyError("Antigravity local path must be a non-empty relative path")
    raw = Path(raw_value.strip())
    if raw.is_absolute():
        raise AgentSafetyError("Antigravity local paths must be relative to the Task workspace")
    resolved_working = working_dir.resolve()
    candidate = (resolved_working / raw).resolve()
    if not _is_within(candidate, resolved_working):
        raise AgentSafetyError("Antigravity local path escaped the Task workspace")
    if must_exist and not candidate.exists():
        raise AgentRuntimeError(f"Local Task workspace path does not exist: {raw_value}")
    return candidate


def _resolve_write_target(
    request: AgentRunRequest,
    raw_value: object,
    *,
    must_exist: bool = False,
) -> Path:
    target = _resolve_relative_path(request.working_dir, raw_value, must_exist=must_exist)
    target = request.working_dir.resolve() / target.relative_to(request.working_dir.resolve())
    target = target.resolve()
    relative = target.relative_to(request.working_dir.resolve()).as_posix()
    if relative.casefold() == "task.md":
        raise AgentSafetyError("Antigravity may not modify protected Task metadata: task.md")
    request_path = Path(relative)
    return request.working_dir.resolve() / request_path


def _fingerprint(path: Path) -> _FileFingerprint:
    if not path.exists():
        return _FileFingerprint(False, None, 0)
    if not path.is_file():
        raise AgentRuntimeError("Antigravity local file operation targeted a non-file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return _FileFingerprint(True, digest.hexdigest(), size)


def _read_small_text(path: Path) -> str | None:
    if not path.exists():
        return ""
    if not path.is_file() or path.stat().st_size > ANTIGRAVITY_FILE_TEXT_LIMIT:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _review_diff(before: str, after: str, relative: str) -> str:
    diff = "\n".join(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="",
    ))
    if len(diff) > ANTIGRAVITY_DIFF_LIMIT:
        raise AgentRuntimeError(
            "Antigravity local file change diff exceeds the safe Master-review limit; split the change into smaller edits"
        )
    return diff


def _atomic_write_text(target: Path, content: str) -> None:
    if not target.parent.is_dir():
        raise AgentRuntimeError("Antigravity local write parent directory disappeared before apply")
    temp = target.with_name(f".{target.name}.nirai-{uuid4().hex}.tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _relative_display(path: Path, working_dir: Path) -> str:
    return path.resolve().relative_to(working_dir.resolve()).as_posix() or "."


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, minimum), maximum)


def _tool_call_summary(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    path = arguments.get("path")
    if isinstance(path, str):
        payload["path"] = path[:1000]
    command = arguments.get("command")
    if isinstance(command, str):
        payload["command"] = command[:4000]
    return payload


def _track_remote_response(active: _ActiveAntigravity, response: dict[str, Any]) -> None:
    interaction_id = response.get("id")
    if isinstance(interaction_id, str) and interaction_id:
        active.interaction_id = interaction_id
        active.interaction_ids.add(interaction_id)
        if len(active.interaction_ids) > ANTIGRAVITY_MAX_INTERACTIONS_PER_SESSION:
            raise AgentRuntimeError("Antigravity exceeded the 128 interaction Session limit")
    environment_id = response.get("environment_id")
    if isinstance(environment_id, str) and environment_id:
        active.environment_id = environment_id


def _required_id(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise AgentRuntimeProtocolError(f"{label} did not provide {key}")
    return value


def _optional_environment_resource_id(payload: dict[str, Any]) -> str | None:
    # Google currently publishes two conflicting Environment REST examples:
    # the API Reference uses `id`, while the managed-agent guide uses
    # `environment_id`. Accept both response shapes so recovery remains safe
    # across either server/schema representation.
    for key in ("id", "environment_id"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _environment_resource_id(payload: dict[str, Any], label: str) -> str:
    value = _optional_environment_resource_id(payload)
    if value is None:
        raise AgentRuntimeProtocolError(
            f"{label} did not provide id or environment_id"
        )
    return value


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"
