from __future__ import annotations

from pathlib import Path
import re


class AgentSafetyError(RuntimeError):
    pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class AgentWorkspacePolicy:
    """Resolve Agent working directories without widening Nirai's write boundary."""

    def __init__(self, root: Path, allowed_dirs: tuple[str, ...]) -> None:
        self.root = root.resolve()
        if not allowed_dirs:
            raise AgentSafetyError("tasks.allowed_dirs must contain at least one directory")
        self.allowed_roots = tuple(self._resolve_configured_root(value) for value in allowed_dirs)
        self.protected_roots = (
            (self.root / "core").resolve(),
            (self.root / "world").resolve(),
        )

    def resolve_working_dir(self, requested: str | None, *, task_id: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id):
            raise AgentSafetyError("task_id contains unsafe characters")

        if requested is None or not requested.strip():
            candidate = (self.allowed_roots[0] / task_id).resolve()
        else:
            raw = Path(requested.strip())
            candidate = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()

        if not any(_is_within(candidate, allowed) for allowed in self.allowed_roots):
            raise AgentSafetyError("working directory is outside tasks.allowed_dirs")

        for protected in self.protected_roots:
            if _is_within(candidate, protected) or _is_within(protected, candidate):
                raise AgentSafetyError(
                    "ordinary Agent Runtime cannot write Nirai core/ or world/; self-build is M5+"
                )

        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    def assert_write_path(self, path: Path, *, working_dir: Path) -> Path:
        resolved_working = working_dir.resolve()
        resolved = path.resolve() if path.is_absolute() else (resolved_working / path).resolve()
        if not _is_within(resolved, resolved_working):
            raise AgentSafetyError("provider file change escaped the Agent working directory")
        for protected in self.protected_roots:
            if _is_within(resolved, protected):
                raise AgentSafetyError("provider file change targeted a protected Nirai source directory")
        return resolved

    def _resolve_configured_root(self, value: str) -> Path:
        cleaned = value.strip()
        if not cleaned:
            raise AgentSafetyError("tasks.allowed_dirs contains an empty directory")
        raw = Path(cleaned)
        return raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True
