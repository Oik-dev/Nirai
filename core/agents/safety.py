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
        self.runtime_root = (self.root / "runtime").resolve()
        self.default_workspace_root = (self.runtime_root / "workspace").resolve()
        self.named_allowed_roots = tuple(
            allowed
            for allowed in self.allowed_roots
            if not _is_within(allowed, self.runtime_root)
        )
        self.protected_roots = (
            (self.root / "core").resolve(),
            (self.root / "world").resolve(),
        )

    def named_working_dir(self, target_name: str, *, task_id: str) -> Path:
        cleaned = target_name.strip()
        if (
            not cleaned
            or len(cleaned) > 255
            or cleaned in {".", ".."}
            or "\x00" in cleaned
            or "/" in cleaned
            or "\\" in cleaned
        ):
            raise AgentSafetyError("Task target folder name is invalid")
        matches = [
            root
            for root in self.named_allowed_roots
            if root.name.casefold() == cleaned.casefold()
        ]
        if not matches:
            available = ", ".join(root.name for root in self.named_allowed_roots)
            raise AgentSafetyError(
                f"Task target folder is not in tasks.allowed_dirs: {cleaned}"
                + (f" (available: {available})" if available else "")
            )
        if len(matches) != 1:
            raise AgentSafetyError(f"Task target folder name is ambiguous: {cleaned}")
        target = matches[0]
        if not target.is_dir():
            raise AgentSafetyError(f"Task target folder does not exist: {cleaned}")
        return self.resolve_working_dir(str(target), task_id=task_id)

    def task_metadata_dir(self, task_id: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id):
            raise AgentSafetyError("task_id contains unsafe characters")
        candidate = (self.default_workspace_root / task_id).resolve()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AgentSafetyError("Agent task metadata directory could not be prepared") from exc
        return candidate

    def resolve_working_dir(self, requested: str | None, *, task_id: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id):
            raise AgentSafetyError("task_id contains unsafe characters")

        create_task_workspace = requested is None or not requested.strip()
        if create_task_workspace:
            candidate = (self.default_workspace_root / task_id).resolve()
        else:
            raw = Path(requested.strip())
            candidate = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()

        if not any(_is_within(candidate, allowed) for allowed in self.allowed_roots):
            raise AgentSafetyError("working directory is outside tasks.allowed_dirs")

        if _is_within(candidate, self.runtime_root):
            own_task_workspace = (self.default_workspace_root / task_id).resolve()
            if candidate != own_task_workspace:
                raise AgentSafetyError(
                    "ordinary Agent Runtime cannot use Nirai internal runtime state as working directory"
                )

        for protected in self.protected_roots:
            if _is_within(candidate, protected) or _is_within(protected, candidate):
                raise AgentSafetyError(
                    "ordinary Agent Runtime cannot write Nirai core/ or world/; self-build is M5+"
                )

        if create_task_workspace:
            try:
                candidate.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise AgentSafetyError("Agent working directory could not be prepared") from exc
        elif not candidate.is_dir():
            raise AgentSafetyError("Agent working directory does not exist")
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

    def prepare_write_path(self, path: Path, *, working_dir: Path) -> Path:
        """Prepare only descendants of an existing Task workspace for a write.

        The Task workspace root itself is never created here. Missing nested
        directories are created one level at a time without ``parents=True`` so
        a concurrently deleted external Project root cannot be recreated as a
        side effect of applying an approved provider change.
        """
        resolved_working = working_dir.resolve()
        if not resolved_working.is_dir():
            raise AgentSafetyError("Agent working directory does not exist")
        resolved = self.assert_write_path(path, working_dir=resolved_working)
        parent = resolved.parent
        try:
            relative_parent = parent.relative_to(resolved_working)
        except ValueError as exc:  # pragma: no cover - assert_write_path already guards this
            raise AgentSafetyError("provider file change escaped the Agent working directory") from exc

        current = resolved_working
        for part in relative_parent.parts:
            current = current / part
            if current.exists():
                if not current.is_dir():
                    raise AgentSafetyError("provider file change parent is not a directory")
                continue
            try:
                current.mkdir(exist_ok=False)
            except OSError as exc:
                raise AgentSafetyError(
                    "Agent working directory disappeared or write parent could not be prepared"
                ) from exc
        if not resolved_working.is_dir():
            raise AgentSafetyError("Agent working directory does not exist")
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
