"""Bound provider detail without truncating the context required for approval."""
from __future__ import annotations

from typing import Any

_EVENT_PAYLOAD_CHAR_BUDGET = 32_000
_EVENT_STRING_LIMIT = 12_000
_EVENT_COLLECTION_LIMIT = 50


def _requires_complete_review_context(event_type: str, payload: dict[str, Any]) -> bool:
    return (
        event_type == "file_change"
        and payload.get("status") == "pending_approval"
        and isinstance(payload.get("operation_id"), str)
        and bool(payload.get("operation_id"))
    )


def _is_session_budget_exempt_event(event_type: str, payload: dict[str, Any]) -> bool:
    if event_type in {"approval_request", "question_request", "plan", "run_state", "error"}:
        return True
    # Approval-correlated File Change context is part of the safety decision.
    # It must remain complete even when ordinary Session detail budget is nearly
    # exhausted, otherwise the Master could approve changes whose paths or diff
    # were truncated from the persisted review context.
    return _requires_complete_review_context(event_type, payload)


def _bounded_event_payload(
    payload: dict[str, Any],
    *,
    char_budget: int = _EVENT_PAYLOAD_CHAR_BUDGET,
    string_limit: int = _EVENT_STRING_LIMIT,
) -> dict[str, Any]:
    budget = [max(0, min(_EVENT_PAYLOAD_CHAR_BUDGET, int(char_budget)))]
    per_string_limit = max(0, min(_EVENT_PAYLOAD_CHAR_BUDGET, int(string_limit)))

    def bound(value: Any, depth: int = 0) -> Any:
        if budget[0] <= 0:
            return None
        if isinstance(value, str):
            limit = min(per_string_limit, budget[0])
            if len(value) <= limit:
                budget[0] -= len(value)
                return value
            clipped = value[: max(0, limit - 1)].rstrip() + "…"
            budget[0] -= len(clipped)
            return clipped
        if isinstance(value, (bool, int, float)) or value is None:
            budget[0] -= min(32, budget[0])
            return value
        if depth >= 5:
            text = str(value)
            return bound(text, depth + 1)
        if isinstance(value, list):
            items: list[Any] = []
            for item in value[:_EVENT_COLLECTION_LIMIT]:
                if budget[0] <= 0:
                    break
                items.append(bound(item, depth + 1))
            if len(value) > len(items) and budget[0] > 0:
                items.append("…truncated…")
                budget[0] -= min(13, budget[0])
            return items
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            items = list(value.items())
            for key, item in items[:_EVENT_COLLECTION_LIMIT]:
                if budget[0] <= 0:
                    break
                result[str(key)] = bound(item, depth + 1)
            if len(items) > len(result) and budget[0] > 0:
                result["_truncated"] = True
                budget[0] -= min(16, budget[0])
            return result
        return bound(str(value), depth + 1)

    bounded = bound(payload)
    return bounded if isinstance(bounded, dict) else {"_truncated": True}
