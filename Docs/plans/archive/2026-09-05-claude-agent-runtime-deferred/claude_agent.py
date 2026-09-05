from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import shutil
from dataclasses import is_dataclass, replace
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    CLIConnectionError,
    CLIJSONDecodeError,
    CLINotFoundError,
    ProcessError,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import (
    PermissionResultAllow,
    PermissionResultDeny,
    PermissionUpdate,
    ToolPermissionContext,
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


LOGGER = logging.getLogger("nirai.core.agent.claude")
CLAUDE_SDK_CLEANUP_TIMEOUT_SEC = 5.0
CLAUDE_SESSION_CLEANUP_RETRIES = 3
CLAUDE_TOOL_OUTPUT_LIMIT = 12_000
CLAUDE_DIFF_LIMIT = 12_000
CLAUDE_TOOLS = ["Read", "Grep", "Glob", "Edit", "Write", "Bash", "AskUserQuestion"]
_CLAUDE_ENV_ALLOWLIST = {
    "ALL_PROXY",
    "APPDATA",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "COMSPEC",
    "HOMEDRIVE",
    "HOMEPATH",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LOCALAPPDATA",
    "NO_PROXY",
    "NUMBER_OF_PROCESSORS",
    "OS",
    "PATH",
    "PATHEXT",
    "PROCESSOR_ARCHITECTURE",
    "PROCESSOR_IDENTIFIER",
    "PROGRAMDATA",
    "PROGRAMFILES",
    "PROGRAMFILES(X86)",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERDOMAIN",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
}
CLAUDE_BLOCKED_TOOLS = [
    "WebFetch",
    "WebSearch",
    "Agent",
    "Skill",
    "NotebookEdit",
    "TodoWrite",
    "TaskCreate",
    "TaskUpdate",
    "TaskGet",
    "TaskList",
    "TaskOutput",
    "TaskStop",
    "ExitPlanMode",
    "ListMcpResources",
    "ReadMcpResource",
    "mcp__*",
]


class ClaudeAgentSdkAdapter:
    provider = "claude-code"
    capabilities = frozenset({"approval", "question", "file_diff", "command_result"})

    def __init__(
        self,
        workspace_policy: AgentWorkspacePolicy,
        *,
        client_factory: Callable[[ClaudeAgentOptions], Any] = ClaudeSDKClient,
    ) -> None:
        self.workspace_policy = workspace_policy
        self.root = workspace_policy.root
        self._client_factory = client_factory
        self._active: dict[str, Any] = {}
        self._active_lock = asyncio.Lock()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        working_dir = request.working_dir.resolve()
        self.workspace_policy.resolve_working_dir(str(working_dir), task_id=request.task_id)
        if request.reasoning_effort == "xhigh":
            # Claude Agent SDK explicitly documents xhigh -> high fallback on
            # unsupported models. Nirai does not silently weaken a Resident's
            # selected reasoning level.
            raise AgentRuntimeProtocolError(
                "Claude Agent SDK may silently downgrade xhigh reasoning on unsupported models; "
                "choose high/max or leave reasoning unset"
            )
        if request.reasoning_effort not in {None, "low", "medium", "high", "max"}:
            raise AgentRuntimeProtocolError(
                f"Claude Agent SDK reasoning value is unavailable: {request.reasoning_effort}"
            )

        session_dir = self._prepare_session_dir(request.agent_session_id)
        settings_path = self._write_session_settings(session_dir)
        stderr_buffer: list[str] = []

        def capture_stderr(text: str) -> None:
            cleaned = str(text).replace("\r", "\\r").replace("\n", "\\n")
            if cleaned:
                stderr_buffer.append(cleaned[:500])
                if len(stderr_buffer) > 8:
                    del stderr_buffer[:-8]
                LOGGER.debug("claude_agent_stderr text=%s", cleaned[:500])

        async def can_use_tool(
            tool_name: str,
            input_data: dict[str, Any],
            context: ToolPermissionContext,
        ) -> PermissionResultAllow | PermissionResultDeny:
            return await self._handle_permission(
                request,
                tool_name,
                input_data,
                context,
                emit=emit,
                wait_for_master=wait_for_master,
            )

        options = ClaudeAgentOptions(
            tools=list(CLAUDE_TOOLS),
            allowed_tools=[],
            disallowed_tools=list(CLAUDE_BLOCKED_TOOLS),
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": self._agent_system_append(request),
            },
            mcp_servers={},
            strict_mcp_config=True,
            permission_mode="default",
            cwd=working_dir,
            settings=str(settings_path),
            env=_claude_environment_overrides(),
            extra_args={
                "restricted": None,
                "safe-mode": None,
                "disable-slash-commands": None,
                "no-session-persistence": None,
                "no-chrome": None,
                "permission-prompts": "host",
            },
            max_buffer_size=2_000_000,
            stderr=capture_stderr,
            can_use_tool=can_use_tool,
            setting_sources=[],
            skills=[],
            plugins=[],
            model=request.model or None,
            fallback_model=None,
            effort=request.reasoning_effort or None,
        )

        client = self._client_factory(options)
        tool_calls: dict[str, tuple[str, dict[str, Any]]] = {}
        final_text: str | None = None
        provider_session_id: str | None = None
        try:
            await client.connect()
            async with self._active_lock:
                self._active[request.agent_session_id] = client
            await client.query(self._agent_prompt(request))

            async for message in client.receive_response():
                if isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        candidate = message.data.get("session_id") or message.data.get("sessionId")
                        if isinstance(candidate, str) and candidate:
                            provider_session_id = candidate
                        payload: dict[str, Any] = {"state": "running"}
                        if provider_session_id:
                            payload["provider_session_id"] = provider_session_id
                        await emit("run_state", payload)
                        await emit("status_message", {
                            "kind": "provider_session_started",
                            "text": "Claude Agent SDK session started",
                        })
                    continue

                if isinstance(message, AssistantMessage):
                    if message.error:
                        raise AgentRuntimeError(f"Claude Agent message failed: {message.error}")
                    for block in message.content:
                        if isinstance(block, ThinkingBlock):
                            # Private reasoning is intentionally never persisted.
                            continue
                        if isinstance(block, TextBlock):
                            # ResultMessage.result is the terminal answer. Keep
                            # intermediate text out of Chat/Memory to avoid dupes.
                            continue
                        if isinstance(block, ToolUseBlock):
                            tool_calls[block.id] = (block.name, dict(block.input))
                            await self._emit_tool_call(block.id, block.name, block.input, working_dir, emit)
                    continue

                if isinstance(message, UserMessage):
                    await self._emit_tool_result(message, tool_calls, working_dir, emit)
                    continue

                if isinstance(message, ResultMessage):
                    if message.session_id:
                        provider_session_id = message.session_id
                    if message.is_error or message.subtype != "success":
                        detail = message.result or "; ".join(message.errors or []) or message.terminal_reason
                        raise AgentRuntimeError(
                            f"Claude Agent SDK ended with {message.subtype}: {detail or 'unknown error'}"
                        )
                    final_text = message.result.strip() if isinstance(message.result, str) else None
                    continue

            if final_text:
                await emit("assistant_message", {
                    "phase": "completed",
                    "message_phase": "final_answer",
                    "text": final_text,
                })
            return final_text or "Claude Agent completed the task"
        except asyncio.CancelledError:
            raise
        except CLINotFoundError as exc:
            raise AgentRuntimeUnavailableError("Claude Agent SDK bundled CLI is unavailable") from exc
        except CLIJSONDecodeError as exc:
            raise AgentRuntimeProtocolError("Claude Agent SDK emitted invalid protocol data") from exc
        except CLIConnectionError as exc:
            raise AgentRuntimeUnavailableError(f"Claude Agent SDK connection failed: {exc}") from exc
        except ProcessError as exc:
            detail = str(exc) or ("; ".join(stderr_buffer[-3:]) if stderr_buffer else "Claude process failed")
            raise AgentRuntimeError(detail) from exc
        except (OSError, RuntimeError) as exc:
            raise AgentRuntimeUnavailableError(f"Claude Agent SDK could not start: {exc}") from exc
        finally:
            async with self._active_lock:
                if self._active.get(request.agent_session_id) is client:
                    self._active.pop(request.agent_session_id, None)
            cleanup_errors: list[str] = []
            try:
                # Run even if connect() failed or was cancelled after spawning
                # the bundled CLI. SDK 0.2.152 bounds its own process escalation.
                # Do not wrap this in asyncio.wait_for: foreign asyncio
                # cancellation can interrupt the SDK kill path.
                await client.disconnect()
            except Exception as exc:
                cleanup_errors.append(f"Claude Agent SDK disconnect failed: {_bounded_text(exc, 300)}")
            try:
                self._cleanup_session_dir(session_dir)
            except AgentRuntimeError as exc:
                cleanup_errors.append(str(exc))
            if cleanup_errors:
                raise AgentRuntimeError("; ".join(cleanup_errors))

    async def cancel(self, agent_session_id: str) -> bool:
        async with self._active_lock:
            client = self._active.get(agent_session_id)
        if client is None:
            return False
        try:
            await asyncio.wait_for(client.interrupt(), timeout=CLAUDE_SDK_CLEANUP_TIMEOUT_SEC)
        except Exception:
            LOGGER.warning("claude_agent_interrupt_failed agent_session_id=%s", agent_session_id, exc_info=True)
            return False
        return True

    async def _handle_permission(
        self,
        request: AgentRunRequest,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> PermissionResultAllow | PermissionResultDeny:
        if context.agent_id is not None:
            return PermissionResultDeny(message="Claude subagents are disabled in this Nirai slice")
        if tool_name in CLAUDE_BLOCKED_TOOLS or tool_name.startswith("mcp__"):
            return PermissionResultDeny(message=f"Claude tool is disabled by Nirai baseline: {tool_name}")

        if tool_name in {"Read", "Grep", "Glob"}:
            # In --restricted mode reads inside cwd are auto-approved. A read
            # that reaches the host permission callback is therefore outside or
            # otherwise outside the baseline and is denied rather than escalated.
            return PermissionResultDeny(message="Reads outside the Task workspace are not allowed")

        if tool_name == "AskUserQuestion":
            return await self._handle_question_permission(
                input_data,
                context,
                emit=emit,
                wait_for_master=wait_for_master,
            )

        if tool_name in {"Edit", "Write"}:
            return await self._handle_file_permission(
                request,
                tool_name,
                input_data,
                context,
                emit=emit,
                wait_for_master=wait_for_master,
            )

        if tool_name == "Bash":
            return await self._handle_command_permission(
                request,
                input_data,
                context,
                emit=emit,
                wait_for_master=wait_for_master,
            )

        return PermissionResultDeny(message=f"Unsupported Claude tool: {tool_name}")

    async def _handle_file_permission(
        self,
        request: AgentRunRequest,
        tool_name: str,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> PermissionResultAllow | PermissionResultDeny:
        raw_path = input_data.get("file_path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return PermissionResultDeny(message="Claude file change did not provide a file path")
        try:
            target = self.workspace_policy.assert_write_path(Path(raw_path), working_dir=request.working_dir)
        except AgentSafetyError as exc:
            await emit("error", {
                "code": "claude_file_change_outside_workspace",
                "message": str(exc),
                "recoverable": False,
            })
            return PermissionResultDeny(message=str(exc))
        relative = target.relative_to(request.working_dir.resolve()).as_posix()
        if relative.casefold() == "task.md":
            return PermissionResultDeny(message="Claude may not modify protected Task metadata: task.md")

        operation_id = context.tool_use_id or f"claude-file-{uuid4()}"
        change_type = "modify" if target.exists() else "create"
        diff = _claude_file_diff(target, tool_name, input_data, relative)
        change: dict[str, Any] = {
            "path": str(target),
            "relative_path": relative,
            "change_type": change_type,
        }
        if diff:
            change["diff"] = diff
        file_payload = {
            "operation_id": operation_id,
            "phase": "proposed",
            "status": "pending_approval",
            "changes": [change],
        }
        await emit("file_change", file_payload)
        options = ["approve_once"]
        session_updates = _session_permission_updates(context, tool_name)
        if session_updates:
            options.append("approve_session")
        options.extend(["reject", "cancel"])
        approval_payload = {
            "request_id": operation_id,
            "operation_id": operation_id,
            "kind": "file_change",
            "title": context.title or f"Claude wants to {tool_name.lower()} {relative}",
            "description": _approval_description(
                context.description or f"Claude Agent SDK tool: {tool_name}",
                session_updates,
            ),
            "grant_root": str(request.working_dir),
            "options": options,
        }
        response = await wait_for_master(operation_id, "approval", approval_payload)
        return _permission_result_from_master(response, input_data, session_updates)

    async def _handle_command_permission(
        self,
        request: AgentRunRequest,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> PermissionResultAllow | PermissionResultDeny:
        command = input_data.get("command")
        if not isinstance(command, str) or not command.strip():
            return PermissionResultDeny(message="Claude Bash call did not provide a command")
        if input_data.get("dangerouslyDisableSandbox") is True:
            return PermissionResultDeny(
                message="Claude may not request an unsandboxed command in Nirai",
                interrupt=True,
            )
        operation_id = context.tool_use_id or f"claude-command-{uuid4()}"
        await emit("command_execution", {
            "operation_id": operation_id,
            "phase": "requested",
            "status": "pending_approval",
            "command": command,
            "cwd": str(request.working_dir),
        })
        options = ["approve_once"]
        session_updates = _session_permission_updates(context, "Bash")
        if session_updates:
            options.append("approve_session")
        options.extend(["reject", "cancel"])
        approval_payload = {
            "request_id": operation_id,
            "operation_id": operation_id,
            "kind": "command",
            "title": context.title or "Claude wants to run a command",
            "description": _approval_description(
                context.description or "Review the exact command before execution",
                session_updates,
            ),
            "command": command,
            "cwd": str(request.working_dir),
            "options": options,
        }
        response = await wait_for_master(operation_id, "approval", approval_payload)
        return _permission_result_from_master(response, input_data, session_updates)

    async def _handle_question_permission(
        self,
        input_data: dict[str, Any],
        context: ToolPermissionContext,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> PermissionResultAllow | PermissionResultDeny:
        raw_questions = input_data.get("questions")
        if not isinstance(raw_questions, list) or not raw_questions:
            return PermissionResultDeny(message="Claude question payload was invalid")
        questions: list[dict[str, Any]] = []
        mappings: list[tuple[str, str, dict[str, str], bool]] = []
        for q_index, raw in enumerate(raw_questions[:4]):
            if not isinstance(raw, dict):
                continue
            text = raw.get("question")
            if not isinstance(text, str) or not text.strip():
                continue
            option_map: dict[str, str] = {}
            options: list[dict[str, Any]] = []
            for o_index, option in enumerate(raw.get("options") or []):
                if not isinstance(option, dict):
                    continue
                label = option.get("label")
                if not isinstance(label, str) or not label:
                    continue
                option_id = f"q{q_index + 1}-o{o_index + 1}"
                option_map[option_id] = label
                options.append({
                    "id": option_id,
                    "label": label,
                    **({"description": option["description"]} if isinstance(option.get("description"), str) else {}),
                })
            if not options:
                continue
            question_id = f"q{q_index + 1}"
            multi = raw.get("multiSelect") is True
            questions.append({
                "id": question_id,
                "question": text,
                "header": raw.get("header") if isinstance(raw.get("header"), str) else None,
                "options": options,
                "allow_multiple": multi,
                # Claude Code's AskUserQuestion supports a user-provided
                # fallback answer in addition to the proposed choices.
                "allow_free_text": True,
            })
            mappings.append((question_id, text, option_map, multi))
        if not questions:
            return PermissionResultDeny(message="Claude question payload had no usable questions")

        request_id = context.tool_use_id or f"claude-question-{uuid4()}"
        payload = {
            "request_id": request_id,
            "title": context.title or "Claude question",
            "questions": questions,
        }
        await emit("question_request", payload)
        response = await wait_for_master(request_id, "question", payload)
        raw_answers = response.get("answers") if isinstance(response, dict) else None
        if not isinstance(raw_answers, dict):
            return PermissionResultDeny(message="Master did not answer Claude's question")

        provider_answers: dict[str, str] = {}
        for question_id, question_text, option_map, multi in mappings:
            values = raw_answers.get(question_id)
            values = values if isinstance(values, list) else []
            labels = [
                option_map.get(value, value).strip()
                for value in values
                if isinstance(value, str) and value.strip()
            ]
            if not labels:
                return PermissionResultDeny(message=f"Claude question was left unanswered: {question_text}")
            if not multi:
                labels = labels[:1]
            provider_answers[question_text] = ", ".join(labels)
        return PermissionResultAllow(updated_input={**input_data, "answers": provider_answers})

    async def _emit_tool_call(
        self,
        tool_use_id: str,
        tool_name: str,
        input_data: dict[str, Any],
        working_dir: Path,
        emit: EmitEvent,
    ) -> None:
        payload: dict[str, Any] = {
            "operation_id": tool_use_id,
            "tool_type": tool_name,
            "phase": "started",
            "status": "running",
        }
        if tool_name == "Bash" and isinstance(input_data.get("command"), str):
            payload["command"] = input_data["command"]
            payload["cwd"] = str(working_dir)
        await emit("tool_call", payload)

    async def _emit_tool_result(
        self,
        message: UserMessage,
        tool_calls: dict[str, tuple[str, dict[str, Any]]],
        working_dir: Path,
        emit: EmitEvent,
    ) -> None:
        blocks = message.content if isinstance(message.content, list) else []
        candidates: list[tuple[str, bool | None, Any]] = []
        for block in blocks:
            if isinstance(block, ToolResultBlock):
                candidates.append((block.tool_use_id, block.is_error, block.content))
        if message.parent_tool_use_id and not candidates:
            candidates.append((message.parent_tool_use_id, None, message.tool_use_result))
        for tool_use_id, is_error, content in candidates:
            call = tool_calls.get(tool_use_id)
            if call is None:
                continue
            tool_name, input_data = call
            status = "failed" if is_error is True else "completed"
            if tool_name == "Bash":
                await emit("command_execution", {
                    "operation_id": tool_use_id,
                    "phase": "completed",
                    "status": status,
                    "command": input_data.get("command"),
                    "cwd": str(working_dir),
                    "output": _bounded_tool_output(content),
                    **_extract_command_result(message.tool_use_result),
                })
            elif tool_name in {"Edit", "Write"}:
                await emit("file_change", {
                    "operation_id": tool_use_id,
                    "phase": "completed",
                    "status": status,
                })

    def _prepare_session_dir(self, agent_session_id: str) -> Path:
        root = self.root / "runtime" / "claude_agent_sessions"
        root.mkdir(parents=True, exist_ok=True)
        for child in root.iterdir():
            if child.name == agent_session_id:
                continue
            self._cleanup_session_dir(child)
        target = root / agent_session_id
        self._cleanup_session_dir(target)
        target.mkdir(parents=True, exist_ok=False)
        return target

    @staticmethod
    def _write_session_settings(session_dir: Path) -> Path:
        settings_path = session_dir / "settings.json"
        settings = {
            "permissions": {
                "ask": ["Bash", "Edit", "Write", "AskUserQuestion"],
                "deny": list(CLAUDE_BLOCKED_TOOLS),
            }
        }
        settings_path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return settings_path

    def _cleanup_session_dir(self, session_dir: Path) -> None:
        last_error: OSError | None = None
        for _ in range(CLAUDE_SESSION_CLEANUP_RETRIES):
            try:
                shutil.rmtree(session_dir, ignore_errors=False)
                if not session_dir.exists():
                    return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
        if session_dir.exists():
            raise AgentRuntimeError(
                f"Claude Agent session cleanup failed: {session_dir.name}: {_bounded_text(last_error, 300)}"
            )

    @staticmethod
    def _agent_system_append(request: AgentRunRequest) -> str:
        return (
            "\nYou are a Nirai Agent Runtime worker. Work only inside the current Task working directory. "
            "Do not use external web access, MCP, subagents, user/project skills, plugins, hooks, or unrelated secrets. "
            "Never modify task.md. Every write/edit/command requiring permission must wait for the Nirai Master. "
            "Do not bypass or weaken permission prompts. Do not expose private chain-of-thought; provide only concise work updates and the final result."
        )

    @staticmethod
    def _agent_prompt(request: AgentRunRequest) -> str:
        return (
            f"Nirai Task {request.task_id} for Resident {request.resident}.\n"
            "Complete the requested work inside the current Task working directory only.\n\n"
            f"Task:\n{request.prompt.strip()}"
        )


def _claude_environment_overrides() -> dict[str, str]:
    overrides: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        overrides[key] = value if upper in _CLAUDE_ENV_ALLOWLIST else ""
    # API-key auth must never silently override the user's Claude subscription.
    overrides["ANTHROPIC_API_KEY"] = ""
    overrides["ANTHROPIC_AUTH_TOKEN"] = ""
    return overrides


def _session_permission_updates(
    context: ToolPermissionContext,
    tool_name: str,
) -> list[PermissionUpdate]:
    updates: list[PermissionUpdate] = []
    for suggestion in context.suggestions:
        if not is_dataclass(suggestion):
            continue
        if (
            getattr(suggestion, "type", None) != "addRules"
            or getattr(suggestion, "behavior", None) != "allow"
            or getattr(suggestion, "mode", None) is not None
            or getattr(suggestion, "directories", None)
        ):
            continue
        rules = getattr(suggestion, "rules", None)
        if not isinstance(rules, list) or not rules:
            continue
        if any(
            getattr(rule, "tool_name", None) != tool_name
            or not isinstance(getattr(rule, "rule_content", None), str)
            or not getattr(rule, "rule_content", "").strip()
            for rule in rules
        ):
            continue
        try:
            updates.append(replace(suggestion, destination="session"))
        except TypeError:
            continue
    return updates


def _approval_description(base: str, updates: list[PermissionUpdate]) -> str:
    scopes: list[str] = []
    for update in updates:
        for rule in update.rules or []:
            if isinstance(rule.rule_content, str):
                scopes.append(f"{rule.tool_name}({rule.rule_content})")
    if not scopes:
        return base
    return base + "\nSession grant if selected: " + ", ".join(scopes[:8])


def _permission_result_from_master(
    response: dict[str, Any],
    input_data: dict[str, Any],
    session_updates: list[PermissionUpdate],
) -> PermissionResultAllow | PermissionResultDeny:
    decision = response.get("decision") if isinstance(response, dict) else None
    if decision == "approve_once":
        return PermissionResultAllow(updated_input=dict(input_data))
    if decision == "approve_session" and session_updates:
        return PermissionResultAllow(
            updated_input=dict(input_data),
            updated_permissions=session_updates,
        )
    if decision == "cancel":
        return PermissionResultDeny(message="Master cancelled the Claude Agent operation", interrupt=True)
    return PermissionResultDeny(message="Master rejected the Claude Agent operation", interrupt=False)


def _claude_file_diff(target: Path, tool_name: str, input_data: dict[str, Any], relative: str) -> str | None:
    try:
        old_text = target.read_text(encoding="utf-8") if target.is_file() and target.stat().st_size <= 1_000_000 else ""
    except (OSError, UnicodeDecodeError):
        return None
    new_text: str | None = None
    if tool_name == "Write":
        content = input_data.get("content")
        if isinstance(content, str):
            new_text = content
    elif tool_name == "Edit":
        old_string = input_data.get("old_string")
        new_string = input_data.get("new_string")
        if isinstance(old_string, str) and isinstance(new_string, str):
            if input_data.get("replace_all") is True:
                new_text = old_text.replace(old_string, new_string)
            elif old_string in old_text:
                new_text = old_text.replace(old_string, new_string, 1)
    if new_text is None:
        return None
    diff = "\n".join(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="",
    ))
    if len(diff) > CLAUDE_DIFF_LIMIT:
        return diff[: CLAUDE_DIFF_LIMIT - 1].rstrip() + "…"
    return diff


def _bounded_tool_output(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            text = str(value)
    if len(text) > CLAUDE_TOOL_OUTPUT_LIMIT:
        return text[: CLAUDE_TOOL_OUTPUT_LIMIT - 1].rstrip() + "…"
    return text


def _extract_command_result(result: object) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in ("exit_code", "exitCode"):
        value = result.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            payload["exit_code"] = value
            break
    for key in ("stdout", "stderr"):
        value = result.get(key)
        if isinstance(value, str):
            payload[key] = _bounded_tool_output(value)
    return payload


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"
