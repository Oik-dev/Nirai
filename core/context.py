"""文脈組み立て（persona + 記憶ブロック / 履歴→messages）"""

from __future__ import annotations

from typing import Any

from serina.core.config import CoreConfig


def _format_memory_line(mem: dict[str, Any]) -> str:
    content = " ".join(str(mem.get("content", "")).split())
    return f"- [{mem.get('type', '?')}] {content}"


def build_system(
    persona: str,
    pinned_mems: list[dict[str, Any]],
    reference_mems: list[dict[str, Any]],
    config: CoreConfig,
) -> str:
    parts = [persona.strip()]

    if pinned_mems:
        core_lines = [
            "## 約束・最優先（不変の核：これが唯一の正典）",
            "以下は継承記憶の正典であり、約束や最優先ルールはこの内容のみを正とすること。"
            "参考記憶で上書きしてはならない。",
        ]
        used = sum(len(line) + 1 for line in core_lines)
        cap = config.pinned_block_char_cap
        for mem in pinned_mems:
            line = _format_memory_line(mem)
            if used + len(line) + 1 > cap:
                break
            core_lines.append(line)
            used += len(line) + 1
        parts.append("\n".join(core_lines))

    ref_mems = [m for m in reference_mems if not m.get("pinned")]
    if ref_mems:
        ref_lines = ["## 関連する記憶（参考）"]
        cap = config.memory_block_char_cap
        used = len(ref_lines[0]) + 1
        for mem in ref_mems:
            line = _format_memory_line(mem)
            if used + len(line) + 1 > cap:
                break
            ref_lines.append(line)
            used += len(line) + 1
        if len(ref_lines) > 1:
            parts.append("\n".join(ref_lines))

    return "\n\n".join(parts)


def to_messages(history: list[dict[str, Any]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for row in history:
        role = str(row.get("role", ""))
        content = str(row.get("content", ""))
        if role and content:
            messages.append({"role": role, "content": content})
    return messages
