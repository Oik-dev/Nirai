from __future__ import annotations

from datetime import datetime
import json
from typing import Any


PROTOCOL_VERSION = 1
CORE_RUNTIME_ID = "nirai-core"
CORE_CAPABILITIES = (
    "semantic-actions-v1",
    "conversation-ui-v1",
    "agent-runtime-ui-v1",
    "resident-roster-v1",
    "holo-addon-v1",
)


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


def runtime_descriptor(runtime_id: str, capabilities: tuple[str, ...] | list[str]) -> dict[str, Any]:
    return {
        "version": PROTOCOL_VERSION,
        "runtime_id": runtime_id,
        "capabilities": list(capabilities),
    }


def parse_runtime_descriptor(value: object) -> tuple[int, str, tuple[str, ...]]:
    if not isinstance(value, dict):
        raise ProtocolError("hello.payload.protocol must be an object")
    version = value.get("version")
    runtime_id = value.get("runtime_id")
    capabilities = value.get("capabilities")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("hello.payload.protocol.version must be an integer")
    if not isinstance(runtime_id, str) or not runtime_id.strip():
        raise ProtocolError("hello.payload.protocol.runtime_id must be a non-empty string")
    if (
        not isinstance(capabilities, list)
        or any(not isinstance(item, str) or not item.strip() for item in capabilities)
        or len(set(capabilities)) != len(capabilities)
    ):
        raise ProtocolError("hello.payload.protocol.capabilities must be a unique array of non-empty strings")
    return version, runtime_id.strip(), tuple(capabilities)


def world_hello_payload(
    secret: str,
    *,
    runtime_id: str = "test-world",
    capabilities: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    return {
        "role": "world",
        "secret": secret,
        "protocol": runtime_descriptor(runtime_id, capabilities),
    }


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
