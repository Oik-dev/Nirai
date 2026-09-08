from __future__ import annotations

import os
from pathlib import Path
import shutil
from typing import Iterable


def _is_usable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _find_on_path(names: Iterable[str]) -> Path | None:
    for name in names:
        found = shutil.which(name)
        if found:
            candidate = Path(found)
            if _is_usable_file(candidate):
                return candidate
    return None


def _newest_existing(paths: Iterable[Path]) -> Path | None:
    candidates: list[tuple[float, str, Path]] = []
    for path in paths:
        try:
            if not _is_usable_file(path):
                continue
            candidates.append((path.stat().st_mtime, str(path).casefold(), path))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


def _codex_desktop_executable(local_app_data: str | None = None) -> Path | None:
    root_text = local_app_data if local_app_data is not None else os.environ.get("LOCALAPPDATA")
    if not root_text:
        return None
    bin_root = Path(root_text) / "OpenAI" / "Codex" / "bin"
    candidates: list[Path] = [bin_root / "codex.exe"]
    try:
        if bin_root.is_dir():
            candidates.extend(child / "codex.exe" for child in bin_root.iterdir() if child.is_dir())
    except OSError:
        pass
    return _newest_existing(candidates)


def _resolve_codex_candidate(path: Path) -> tuple[str, ...] | None:
    if not _is_usable_file(path):
        return None
    if path.suffix.casefold() == ".cmd":
        node_path = _find_on_path(("node.exe", "node"))
        codex_js = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if node_path is None or not _is_usable_file(codex_js):
            return None
        return (str(node_path), str(codex_js))
    return (str(path),)


def resolve_codex_command(*, local_app_data: str | None = None) -> tuple[str, ...] | None:
    """Resolve a usable Codex CLI command without invoking it.

    Resolution order:
    1. PATH native/launcher install.
    2. Codex Desktop managed runtime under LocalAppData.

    npm .cmd launchers are converted to an explicit node + codex.js command so
    Doctor and product runtime validate the same underlying executable.
    """
    path_candidate = _find_on_path(("codex.exe", "codex.cmd", "codex"))
    if path_candidate is not None:
        command = _resolve_codex_candidate(path_candidate)
        if command is not None:
            return command

    desktop_candidate = _codex_desktop_executable(local_app_data)
    if desktop_candidate is not None:
        return _resolve_codex_candidate(desktop_candidate)
    return None


def format_command(command: tuple[str, ...] | None) -> str | None:
    if command is None:
        return None
    return " ".join(command)
