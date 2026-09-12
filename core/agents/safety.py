from __future__ import annotations

import os
from pathlib import Path
import re


class AgentSafetyError(RuntimeError):
    pass


# Nirai-wide Master escalation threshold for destructive local deletion. Normal
# edits and cleanup continue automatically; only broad deletion stops the
# workflow for a direct Master decision.
MAJOR_DESTRUCTIVE_DELETE_FILE_COUNT = 25
MAJOR_DESTRUCTIVE_DELETE_BYTE_COUNT = 100_000_000
MAJOR_DESTRUCTIVE_DELETE_RATIO_MIN_FILES = 10
MAJOR_DESTRUCTIVE_DELETE_RATIO_NUMERATOR = 1
MAJOR_DESTRUCTIVE_DELETE_RATIO_DENOMINATOR = 2


def count_workspace_regular_files(root: Path) -> int:
    resolved_root = root.resolve()
    count = 0
    for current_raw, dirnames, filenames in os.walk(
        resolved_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_raw)
        kept_dirs: list[str] = []
        for name in dirnames:
            child = current / name
            is_junction = getattr(child, "is_junction", lambda: False)
            if child.is_symlink() or is_junction():
                continue
            try:
                if _is_within(child.resolve(), resolved_root):
                    kept_dirs.append(name)
            except OSError:
                continue
        dirnames[:] = kept_dirs
        for name in filenames:
            child = current / name
            if child.is_symlink():
                continue
            try:
                if child.is_file() and _is_within(child.resolve(), resolved_root):
                    count += 1
            except OSError:
                continue
    return count


def requires_master_for_destructive_delete(
    *,
    delete_count: int,
    deleted_bytes: int,
    baseline_file_count: int,
) -> bool:
    if delete_count >= MAJOR_DESTRUCTIVE_DELETE_FILE_COUNT:
        return True
    if deleted_bytes >= MAJOR_DESTRUCTIVE_DELETE_BYTE_COUNT:
        return True
    return (
        delete_count >= MAJOR_DESTRUCTIVE_DELETE_RATIO_MIN_FILES
        and baseline_file_count > 0
        and delete_count * MAJOR_DESTRUCTIVE_DELETE_RATIO_DENOMINATOR
        >= baseline_file_count * MAJOR_DESTRUCTIVE_DELETE_RATIO_NUMERATOR
    )


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_INTEGRATED_AUDIT_PROTECTED_TOP_LEVEL = frozenset({
    ".git",
    "runtime",
    "avatars",
    "material",
    "world_memory",
    "node_modules",
})


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

    def named_integrated_audit_working_dir(self, target_name: str, *, task_id: str) -> Path:
        """Resolve a writable target for an explicitly-scoped integrated audit.

        External projects retain the ordinary ``tasks.allowed_dirs`` boundary.
        Nirai's own repository root is a special case available only to ``IA-*``
        tasks; ordinary Agent work remains unable to write Core/World source.
        """
        cleaned = target_name.strip()
        if (
            not cleaned
            or len(cleaned) > 255
            or cleaned in {".", ".."}
            or "\x00" in cleaned
            or "/" in cleaned
            or "\\" in cleaned
        ):
            raise AgentSafetyError("Integrated Audit target folder name is invalid")
        if self.root.name.casefold() == cleaned.casefold():
            return self.resolve_integrated_audit_working_dir(str(self.root), task_id=task_id)
        return self.named_working_dir(cleaned, task_id=task_id)

    def named_review_working_dir(self, target_name: str, *, task_id: str) -> Path:
        """Resolve a read-only review target without widening Agent write roots.

        The Nirai repository root itself may be reviewed by basename because the
        Cursor review path operates only on an isolated staging copy and never
        applies provider changes. External projects still require an exact
        configured ``tasks.allowed_dirs`` root basename.
        """
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
        if self.root.name.casefold() == cleaned.casefold():
            if not self.root.is_dir():
                raise AgentSafetyError(f"Review target folder does not exist: {cleaned}")
            return self.root
        matches = [
            root
            for root in self.named_allowed_roots
            if root.name.casefold() == cleaned.casefold()
        ]
        if not matches:
            available = ", ".join((self.root.name, *(root.name for root in self.named_allowed_roots)))
            raise AgentSafetyError(
                f"Review target folder is not available: {cleaned}"
                + (f" (available: {available})" if available else "")
            )
        if len(matches) != 1:
            raise AgentSafetyError(f"Review target folder name is ambiguous: {cleaned}")
        target = matches[0]
        return self.resolve_read_only_working_dir(str(target), task_id=task_id)

    def resolve_integrated_audit_working_dir(self, requested: str, *, task_id: str) -> Path:
        if not _SAFE_ID.fullmatch(task_id) or not task_id.startswith("IA-"):
            raise AgentSafetyError("Integrated Audit requires an IA-* task_id")
        cleaned = requested.strip()
        if not cleaned:
            raise AgentSafetyError("Integrated Audit working directory must not be empty")
        raw = Path(cleaned)
        candidate = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()
        if candidate == self.root:
            if not candidate.is_dir():
                raise AgentSafetyError("Integrated Audit Nirai root does not exist")
            return candidate
        return self.resolve_working_dir(str(candidate), task_id=task_id)

    def resolve_read_only_working_dir(self, requested: str, *, task_id: str) -> Path:
        """Resolve an existing review source without granting write authority."""
        if not _SAFE_ID.fullmatch(task_id):
            raise AgentSafetyError("task_id contains unsafe characters")
        cleaned = requested.strip()
        if not cleaned:
            raise AgentSafetyError("read-only working directory must not be empty")
        raw = Path(cleaned)
        candidate = raw.resolve() if raw.is_absolute() else (self.root / raw).resolve()

        if candidate == self.root:
            if not candidate.is_dir():
                raise AgentSafetyError("read-only working directory does not exist")
            return candidate
        if not any(_is_within(candidate, allowed) for allowed in self.allowed_roots):
            raise AgentSafetyError("read-only working directory is outside tasks.allowed_dirs")
        if _is_within(candidate, self.runtime_root):
            own_task_workspace = (self.default_workspace_root / task_id).resolve()
            if candidate != own_task_workspace:
                raise AgentSafetyError(
                    "read-only Agent Runtime cannot inspect unrelated Nirai runtime state"
                )
        if not candidate.is_dir():
            raise AgentSafetyError("read-only working directory does not exist")
        return candidate

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
        if resolved_working == self.root:
            relative = resolved.relative_to(self.root)
            if relative.parts:
                top = relative.parts[0].casefold()
                if (
                    top in _INTEGRATED_AUDIT_PROTECTED_TOP_LEVEL
                    or top == ".env"
                    or top.startswith(".env.")
                ):
                    raise AgentSafetyError(
                        "Integrated Audit cannot modify Nirai generated, credential, or asset state"
                    )
            return resolved
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
