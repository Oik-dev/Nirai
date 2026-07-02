"""人格ファイルの読み込み（ロジックを持たない）"""

from __future__ import annotations

from pathlib import Path

DEFAULT_PERSONA_PATH = Path(__file__).resolve().parent / "persona.md"


def load_persona(path: Path | str | None = None) -> str:
    persona_path = Path(path) if path else DEFAULT_PERSONA_PATH
    return persona_path.read_text(encoding="utf-8")
