from __future__ import annotations

from datetime import datetime
import json
from typing import Any


class ProtocolError(ValueError):
    """Raised when a WebSocket message doesn't match Nirai's envelope."""


def now_iso_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def time_of_day(now: datetime | None = None) -> str:
    hour = (now or datetime.now().astimezone()).hour
    if 5 <= hour < 9:
        return "morning"
    if 9 <= hour < 16:
        return "day"
    if 16 <= hour < 19:
        return "evening"
    return "night"


def make_message(message_type: str, payload: dict[str, Any], message_id: str | None = None) -> str:
    message: dict[str, Any] = {
        "type": message_type,
        "ts": now_iso_local(),
        "payload": payload,
    }
    if message_id is not None:
        message["id"] = message_id
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def parse_message(raw: str) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError("message is not valid JSON") from exc

    if not isinstance(message, dict):
        raise ProtocolError("message must be an object")
    if not isinstance(message.get("type"), str) or not message["type"]:
        raise ProtocolError("message.type must be a non-empty string")
    if not isinstance(message.get("ts"), str) or not message["ts"]:
        raise ProtocolError("message.ts must be a non-empty string")
    if not isinstance(message.get("payload"), dict):
        raise ProtocolError("message.payload must be an object")
    if "id" in message and not isinstance(message["id"], str):
        raise ProtocolError("message.id must be a string when present")
    return message
