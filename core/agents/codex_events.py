from __future__ import annotations

from pathlib import Path
from typing import Any

from .safety import AgentWorkspacePolicy
from .types import AgentEventType


NormalizedEvent = tuple[AgentEventType, dict[str, Any]]


def normalize_codex_item(
    item: object,
    *,
    phase: str,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> list[NormalizedEvent]:
    if not isinstance(item, dict):
        return []
    item_type = item.get("type")
    item_id = item.get("id") if isinstance(item.get("id"), str) else None
    common = {"operation_id": item_id, "phase": phase}

    if item_type == "agentMessage":
        text = item.get("text")
        if not isinstance(text, str) or not text:
            return []
        return [("assistant_message", {
            **common,
            "text": text,
            "message_phase": item.get("phase") if isinstance(item.get("phase"), str) else None,
        })]

    if item_type == "commandExecution":
        return [("command_execution", {
            **common,
            "command": _string(item.get("command")),
            "cwd": _string(item.get("cwd")),
            "status": _string(item.get("status")),
            "output": _optional_string(item.get("aggregatedOutput")),
            "exit_code": item.get("exitCode") if isinstance(item.get("exitCode"), int) else None,
            "duration_ms": item.get("durationMs") if isinstance(item.get("durationMs"), int) else None,
            "actions": item.get("commandActions") if isinstance(item.get("commandActions"), list) else [],
        })]

    if item_type == "fileChange":
        raw_changes = item.get("changes")
        changes: list[dict[str, Any]] = []
        if isinstance(raw_changes, list):
            for raw_change in raw_changes:
                if not isinstance(raw_change, dict):
                    continue
                raw_path = raw_change.get("path")
                if not isinstance(raw_path, str) or not raw_path:
                    continue
                resolved = workspace_policy.assert_write_path(Path(raw_path), working_dir=working_dir)
                changes.append({
                    "path": str(resolved),
                    "relative_path": _relative_display(resolved, working_dir),
                    "diff": _string(raw_change.get("diff")),
                })
        return [("file_change", {
            **common,
            "status": _string(item.get("status")),
            "changes": changes,
        })]

    if item_type in {"mcpToolCall", "dynamicToolCall", "webSearch"}:
        tool_type = {
            "mcpToolCall": "mcp",
            "dynamicToolCall": "dynamic",
            "webSearch": "web_search",
        }[item_type]
        payload: dict[str, Any] = {**common, "tool_type": tool_type}
        for key in (
            "server",
            "tool",
            "status",
            "arguments",
            "result",
            "error",
            "query",
            "action",
            "durationMs",
            "success",
        ):
            if key in item:
                payload[key] = item[key]
        return [("tool_call", payload)]

    if item_type == "plan":
        text = item.get("text")
        if not isinstance(text, str) or not text:
            return []
        return [("plan", {**common, "text": text})]

    if item_type in {"collabAgentToolCall", "subAgentActivity"}:
        payload = {
            **common,
            "subagent_type": "collaboration" if item_type == "collabAgentToolCall" else "activity",
        }
        for key in (
            "tool",
            "status",
            "prompt",
            "model",
            "reasoningEffort",
            "agentsStates",
            "agentPath",
            "kind",
        ):
            if key in item:
                payload[key] = item[key]
        return [("subagent_update", payload)]

    if item_type in {"imageGeneration", "imageView"}:
        payload = {
            **common,
            "artifact_type": "image_generation" if item_type == "imageGeneration" else "image_view",
        }
        for key in ("savedPath", "path", "status", "revisedPrompt"):
            if key in item:
                payload[key] = item[key]
        return [("artifact", payload)]

    # Deliberately do not surface raw `reasoning` items or reasoning deltas.
    return []


def normalize_codex_notification(
    method: object,
    params: object,
    *,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> list[NormalizedEvent]:
    if not isinstance(method, str) or not isinstance(params, dict):
        return []

    if method in {"item/started", "item/completed"}:
        return normalize_codex_item(
            params.get("item"),
            phase="started" if method == "item/started" else "completed",
            working_dir=working_dir,
            workspace_policy=workspace_policy,
        )

    if method in {
        "item/commandExecution/outputDelta",
        "item/fileChange/outputDelta",
        "item/fileChange/patchUpdated",
    }:
        # Streaming deltas can create an unbounded event log and duplicate the
        # completed command/file payload. Nirai persists the bounded completed
        # event instead; live progress remains visible through run_state.
        return []

    if method == "turn/diff/updated":
        diff = params.get("diff")
        if not isinstance(diff, str):
            return []
        return [("diff", {"diff": diff})]

    if method == "turn/plan/updated":
        raw_plan = params.get("plan")
        plan = raw_plan if isinstance(raw_plan, list) else []
        payload = {
            "explanation": _optional_string(params.get("explanation")),
            "steps": plan,
        }
        return [("plan", payload), ("todo_update", payload)]

    if method == "error":
        error = params.get("error")
        message = "Codex reported an error"
        code: str | int | None = None
        if isinstance(error, dict):
            if isinstance(error.get("message"), str) and error["message"]:
                message = error["message"]
            if isinstance(error.get("code"), (str, int)):
                code = error["code"]
        elif isinstance(error, str) and error:
            message = error
        payload: dict[str, Any] = {
            "message": message,
            "will_retry": bool(params.get("willRetry")),
            "recoverable": bool(params.get("willRetry")),
        }
        if code is not None:
            payload["code"] = code
        return [("error", payload)]

    if method in {"warning", "deprecationNotice", "configWarning"}:
        text = params.get("message")
        if not isinstance(text, str) or not text:
            text = params.get("text")
        if not isinstance(text, str) or not text:
            text = "Codex reported a configuration notice."
        kind = {
            "warning": "warning",
            "deprecationNotice": "deprecation",
            "configWarning": "configuration",
        }[method]
        return [("status_message", {"kind": kind, "text": text})]

    return []


def _relative_display(path: Path, working_dir: Path) -> str:
    try:
        return path.relative_to(working_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _string(value: object) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
