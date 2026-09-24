from __future__ import annotations

from pathlib import Path


WORLD_RULES_FILENAME = "WORLD_RULES.md"
WORLD_RULES_MAX_CHARS = 4_000


def load_world_rules() -> str:
    path = Path(__file__).resolve().parent.parent / WORLD_RULES_FILENAME
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("Nirai World Rules are empty")
    if len(text) > WORLD_RULES_MAX_CHARS:
        raise RuntimeError("Nirai World Rules exceed the safe prompt limit")
    return text


def world_rules_prompt_block() -> str:
    return f"<nirai-world-rules>\n{load_world_rules()}\n</nirai-world-rules>"


def apply_world_rules(prompt: str) -> str:
    return f"{world_rules_prompt_block()}\n\n{prompt.strip()}"
