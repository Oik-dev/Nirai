from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .safety import AgentSafetyError, AgentWorkspacePolicy
from .types import AgentEventType


NormalizedEvent = tuple[AgentEventType, dict[str, Any]]


def normalize_cursor_session_update(
    update: object,
    *,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> list[NormalizedEvent]:
    """Map stable ACP session/update variants into Nirai common Agent events.

    agent_message_chunk is aggregated by the adapter and emitted once at turn end.
    agent_thought_chunk is intentionally discarded so private reasoning never enters
    Nirai persistence or World protocol.
    """
    if not isinstance(update, dict):
        return []
    kind = update.get("sessionUpdate")
    if kind in {"agent_message_chunk", "agent_thought_chunk"}:
        return []

    if kind in {"tool_call", "tool_call_update"}:
        return _normalize_tool_call(
            update,
            phase="started" if kind == "tool_call" else "updated",
            working_dir=working_dir,
            workspace_policy=workspace_policy,
        )

    if kind == "plan":
        entries = update.get("entries")
        steps = entries if isinstance(entries, list) else []
        return [("plan", {"steps": steps}), ("todo_update", {"steps": steps})]

    if kind == "session_info_update":
        title = update.get("title")
        if isinstance(title, str) and title:
            return [("status_message", {"kind": "session_info", "text": title})]

    if kind == "usage_update":
        # Usage display is not part of the M4 P0 Agent Event contract yet.
        return []

    # current_mode_update, config_option_update, available_commands_update and
    # future ACP display metadata do not affect the common Agent runtime state.
    return []


def normalize_cursor_todos(params: object) -> list[NormalizedEvent]:
    if not isinstance(params, dict):
        return []
    todos = params.get("todos")
    if not isinstance(todos, list):
        return []
    return [("todo_update", {
        "operation_id": _optional_string(params.get("toolCallId")),
        "steps": todos,
        "merge": params.get("merge") is True,
    })]


def normalize_cursor_task(params: object) -> list[NormalizedEvent]:
    if not isinstance(params, dict):
        return []
    payload: dict[str, Any] = {
        "operation_id": _optional_string(params.get("toolCallId")),
        "subagent_type": params.get("subagentType"),
    }
    for key, target in (
        ("description", "description"),
        ("prompt", "prompt"),
        ("model", "model"),
        ("agentId", "agent_id"),
        ("durationMs", "duration_ms"),
    ):
        value = params.get(key)
        if value is not None:
            payload[target] = value
    return [("subagent_update", payload)]


def normalize_cursor_image(
    params: object,
    *,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> list[NormalizedEvent]:
    if not isinstance(params, dict):
        return []
    payload: dict[str, Any] = {
        "operation_id": _optional_string(params.get("toolCallId")),
        "artifact_type": "image_generation",
    }
    description = params.get("description")
    if isinstance(description, str):
        payload["description"] = description
    file_path = params.get("filePath")
    if isinstance(file_path, str) and file_path:
        resolved = _resolve_workspace_path(
            file_path,
            working_dir=working_dir,
            workspace_policy=workspace_policy,
        )
        payload["path"] = str(resolved)
        payload["relative_path"] = _relative_display(resolved, working_dir)
    return [("artifact", payload)]


def cursor_message_chunk_text(update: object) -> str | None:
    if not isinstance(update, dict) or update.get("sessionUpdate") != "agent_message_chunk":
        return None
    content = update.get("content")
    if not isinstance(content, dict) or content.get("type") != "text":
        return None
    text = content.get("text")
    return text if isinstance(text, str) and text else None


def cursor_permission_paths(tool_call: object) -> list[str]:
    """Extract file locations surfaced by ACP without guessing shell arguments."""
    if not isinstance(tool_call, dict):
        return []
    values: list[str] = []
    locations = tool_call.get("locations")
    if isinstance(locations, list):
        for location in locations:
            if not isinstance(location, dict):
                continue
            path_value = location.get("path") or location.get("uri")
            if isinstance(path_value, str) and path_value:
                values.append(path_value)
    raw_input = tool_call.get("rawInput")
    if isinstance(raw_input, dict):
        for key in ("path", "filePath", "file_path", "source", "target", "oldPath", "newPath"):
            value = raw_input.get(key)
            if isinstance(value, str) and value:
                values.append(value)
    return list(dict.fromkeys(values))


def validate_cursor_tool_paths(
    tool_call: object,
    *,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> None:
    if not isinstance(tool_call, dict):
        return
    kind = str(tool_call.get("kind") or "").casefold()
    if kind not in {"read", "edit", "delete", "move", "write"}:
        return
    for raw_path in cursor_permission_paths(tool_call):
        _resolve_workspace_path(
            raw_path,
            working_dir=working_dir,
            workspace_policy=workspace_policy,
        )


def _normalize_tool_call(
    update: dict[str, Any],
    *,
    phase: str,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> list[NormalizedEvent]:
    operation_id = _optional_string(update.get("toolCallId"))
    tool_kind = _optional_string(update.get("kind")) or "other"
    status = _optional_string(update.get("status"))
    title = _optional_string(update.get("title"))
    raw_input = update.get("rawInput") if isinstance(update.get("rawInput"), dict) else {}
    raw_output = update.get("rawOutput")

    common: dict[str, Any] = {
        "operation_id": operation_id,
        "phase": phase,
        "tool_type": tool_kind,
        "status": status,
    }
    if title:
        common["title"] = title

    lowered = tool_kind.casefold()
    if lowered in {"execute", "shell", "terminal"}:
        command = raw_input.get("command") if isinstance(raw_input, dict) else None
        payload = {
            **common,
            "command": command if isinstance(command, str) else (title or ""),
            "cwd": str(working_dir),
        }
        if raw_output is not None:
            payload["output"] = raw_output
        return [("command_execution", payload)]

    if lowered in {"edit", "delete", "move", "write"}:
        changes: list[dict[str, Any]] = []
        for raw_path in cursor_permission_paths(update):
            resolved = _resolve_workspace_path(
                raw_path,
                working_dir=working_dir,
                workspace_policy=workspace_policy,
            )
            changes.append({
                "path": str(resolved),
                "relative_path": _relative_display(resolved, working_dir),
            })
        return [("file_change", {**common, "changes": changes})]

    return [("tool_call", {
        **common,
        **({"arguments": raw_input} if raw_input else {}),
        **({"result": raw_output} if raw_output is not None else {}),
    })]


def _resolve_workspace_path(
    raw_path: str,
    *,
    working_dir: Path,
    workspace_policy: AgentWorkspacePolicy,
) -> Path:
    cleaned = raw_path.strip()
    if cleaned.casefold().startswith("file:"):
        parsed = urlparse(cleaned)
        cleaned = unquote(parsed.path)
        if parsed.netloc:
            cleaned = f"//{parsed.netloc}{cleaned}"
        if len(cleaned) >= 3 and cleaned[0] == "/" and cleaned[2] == ":":
            cleaned = cleaned[1:]
    candidate = Path(cleaned)
    return workspace_policy.assert_write_path(candidate, working_dir=working_dir)


def _relative_display(path: Path, working_dir: Path) -> str:
    try:
        return path.relative_to(working_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
