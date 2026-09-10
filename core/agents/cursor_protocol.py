"""Cursor model selection and permission-value conversion; no runtime state."""
from __future__ import annotations

import re
from typing import Any

from .base import AgentRunRequest, AgentRuntimeProtocolError
from .cursor_policy import CURSOR_EXTERNAL_TOOL_KINDS


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


def _bounded_text(value: object, limit: int) -> str:
    text = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return text if len(text) <= limit else text[:limit] + "…"
