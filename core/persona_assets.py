"""人格資産のブロック結合ローダ（合意台帳 §3.9 / 設計書 §4.8）。

`prompt/persona/manifest.toml` の順でブロックを結合し、
従来の `persona.md`＋`boundary.md` と同じ注入形を返す。
自律改訂ロジックは持たない（Wave 4）。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PERSONA_DIR = ROOT / "prompt" / "persona"
DEFAULT_MANIFEST = DEFAULT_PERSONA_DIR / "manifest.toml"


@dataclass(frozen=True)
class PersonaBlock:
    id: str
    file: str
    mutable: bool
    max_chars: int
    role: str  # "persona" | "absolute_rules"
    text: str


@dataclass(frozen=True)
class PersonaAssets:
    blocks: tuple[PersonaBlock, ...]
    persona_text: str
    absolute_rules: str


def load_persona_assets(persona_dir: Path | str | None = None) -> PersonaAssets:
    """manifest に従いブロックを読み、persona_text と absolute_rules を返す。"""
    directory = Path(persona_dir) if persona_dir else DEFAULT_PERSONA_DIR
    manifest_path = directory / "manifest.toml"
    raw = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    blocks: list[PersonaBlock] = []
    persona_parts: list[str] = []
    absolute_rules = ""

    for entry in raw["blocks"]:
        role = entry.get("role", "persona")
        text = (directory / entry["file"]).read_text(encoding="utf-8")
        block = PersonaBlock(
            id=entry["id"],
            file=entry["file"],
            mutable=bool(entry["mutable"]),
            max_chars=int(entry.get("max_chars", 0) or 0),
            role=role,
            text=text,
        )
        blocks.append(block)
        if role == "absolute_rules":
            absolute_rules = text
        else:
            persona_parts.append(text)

    if not absolute_rules:
        raise ValueError("manifest に role=absolute_rules のブロックが無い")

    return PersonaAssets(
        blocks=tuple(blocks),
        persona_text="".join(persona_parts),
        absolute_rules=absolute_rules,
    )
