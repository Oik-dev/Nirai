"""Cursor staging, frozen review bundles and approved apply/rollback.

The Adapter owns lifecycle and runtime-id claims. This implementation mixin uses
that same owner so cancellation and existing fault-injection hooks stay intact.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import AgentRunRequest, AgentRuntimeError, AgentRuntimeUnavailableError, EmitEvent, WaitForMaster
from .safety import AgentSafetyError, AgentWorkspacePolicy, requires_master_for_destructive_delete
from .cursor_protocol import _bounded_text
from .cursor_policy import (
    CURSOR_STAGE_CLEANUP_RETRIES, CURSOR_STALE_RUNTIME_AGE_SEC,
    CURSOR_STAGE_FILE_LIMIT, CURSOR_STAGE_BYTE_LIMIT, CURSOR_DIFF_TEXT_FILE_LIMIT,
    CURSOR_WRITABLE_IGNORE_NAMES, CURSOR_READ_ONLY_IGNORE_NAMES, CURSOR_NIRAI_REVIEW_IGNORE_NAMES,
)

LOGGER = logging.getLogger("nirai.core.agent.cursor_acp")
CURSOR_WORKSPACE_IGNORE_FILE = ".niraiignore"
CURSOR_WORKSPACE_IGNORE_MAX_PATTERNS = 128
CURSOR_WORKSPACE_IGNORE_MAX_PATTERN_LENGTH = 128


class StagedApplyCancelledAfterCommit(asyncio.CancelledError):
    """Cancellation arrived after the reviewed staged diff reached the real workspace."""


class CursorWorkspaceMixin:
    """File operations on the Adapter's single workspace and ownership state."""

    root: Path
    workspace_policy: AgentWorkspacePolicy
    _preparing_ids: set[str]

    async def _prepare_staging_workspace_cancellation_safe(
        self,
        agent_session_id: str,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str],
        stable_key: str | None,
        staging_root: Path | None = None,
    ) -> tuple[Path, dict[str, tuple[int, str]]]:
        prepare_task = asyncio.create_task(
            asyncio.to_thread(
                self._prepare_staging_workspace,
                agent_session_id,
                working_dir,
                ignore_parts=ignore_parts,
                stable_key=stable_key,
                staging_root=staging_root,
            ),
            name=f"cursor-stage-prepare-{agent_session_id}",
        )
        try:
            return await asyncio.shield(prepare_task)
        except asyncio.CancelledError:
            # to_thread workers cannot be force-cancelled. Wait for a late stage
            # to materialize, remove it, then let the outer owner release claims.
            try:
                staging_dir, _baseline = await prepare_task
            except BaseException:
                staging_dir = None
            if staging_dir is not None:
                try:
                    await asyncio.to_thread(self._cleanup_staging_workspace, staging_dir)
                except AgentRuntimeError:
                    LOGGER.warning(
                        "cursor_cancelled_prepare_cleanup_failed agent_session_id=%s",
                        agent_session_id,
                        exc_info=True,
                    )
            raise

    async def _review_and_apply_staged_changes(
        self,
        request: AgentRunRequest,
        *,
        staging_dir: Path,
        review_dir: Path,
        baseline: dict[str, tuple[int, str]],
        ignore_parts: frozenset[str] = CURSOR_WRITABLE_IGNORE_NAMES,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
        provider_label: str = "Cursor",
        operation_prefix: str = "cursor-stage-apply",
        auto_apply_kind: str = "cursor_stage_auto_apply",
    ) -> None:
        changes, reviewed_staging, reviewed_bundle = await asyncio.to_thread(
            self._freeze_staged_changes,
            request.working_dir,
            staging_dir,
            review_dir,
            baseline,
            ignore_parts=ignore_parts,
        )
        if not changes:
            return

        operation_id = f"{operation_prefix}-{request.agent_session_id}"
        review_changes = _cursor_review_manifest(changes)
        requires_master = self._staged_changes_require_master_approval(changes, baseline)
        file_payload = {
            "operation_id": operation_id,
            "phase": "staged",
            "status": "pending_approval" if requires_master else "pending_apply",
            "changes": review_changes,
        }
        await emit("file_change", file_payload)
        if requires_master:
            approval_payload = {
                "request_id": operation_id,
                "operation_id": operation_id,
                "kind": "file_change",
                "title": f"{provider_label} staged changes contain large destructive deletion",
                "description": (
                    f"{provider_label} worked only in an isolated staging workspace. "
                    "This diff deletes a substantial part of the Task workspace. Apply it?"
                ),
                "grant_root": str(request.working_dir),
                "options": ["approve_once", "reject", "cancel"],
            }
            await emit("approval_request", approval_payload)
            response = await wait_for_master(operation_id, "approval", approval_payload)
            decision = response.get("decision") if isinstance(response, dict) else None
            if decision == "cancel":
                raise asyncio.CancelledError
            if decision != "approve_once":
                raise AgentRuntimeError(
                    f"Master rejected large destructive {provider_label} staged changes; the Task workspace was not modified"
                )
        else:
            await emit("status_message", {
                "kind": auto_apply_kind,
                "text": f"{provider_label} staged changes passed Nirai safety checks and will be applied automatically",
                "operation_id": operation_id,
            })

        # Apply only the frozen review bundle, never a live staging directory.
        # Detect orphan/helper writes between freeze and apply and refuse if
        # either the staging tree or the reviewed bundle changed.
        if await asyncio.to_thread(
            self._workspace_snapshot,
            staging_dir,
            ignore_parts=ignore_parts,
        ) != reviewed_staging:
            raise AgentRuntimeError(
                "Cursor staging workspace changed after review; approved changes were not applied"
            )
        if await asyncio.to_thread(
            self._workspace_snapshot,
            review_dir,
            ignore_parts=ignore_parts,
        ) != reviewed_bundle:
            raise AgentRuntimeError(
                "Cursor staged review bundle changed after review; approved changes were not applied"
            )

        apply_task = asyncio.create_task(
            asyncio.to_thread(
                self._apply_staged_changes,
                request.working_dir,
                review_dir,
                baseline,
                changes,
                ignore_parts=ignore_parts,
            ),
            name=f"cursor-stage-apply-{request.agent_session_id}",
        )
        cancelled_during_apply = False
        try:
            # Cancelling the asyncio waiter cannot stop a worker thread that is
            # already writing user files. Shield it and keep the session-owned
            # staging/recovery resources alive until apply or rollback settles.
            await asyncio.shield(apply_task)
        except asyncio.CancelledError:
            cancelled_during_apply = True
            try:
                await apply_task
            except Exception:
                # A failed apply/rollback is more important than the cancel
                # request because it may require recovery action.
                raise
        await emit("file_change", {
            **file_payload,
            "phase": "completed",
            "status": "completed",
        })
        if cancelled_during_apply:
            # Cancellation cannot stop a worker thread that already entered the
            # approved write. Wait until apply/rollback is stable and emit the
            # completed file-change evidence above, then propagate a specialized
            # CancelledError so callers can preserve the committed-work fact.
            raise StagedApplyCancelledAfterCommit

    @staticmethod
    def _staged_changes_require_master_approval(
        changes: list[dict[str, Any]],
        baseline: dict[str, tuple[int, str]],
    ) -> bool:
        deleted = [
            change
            for change in changes
            if change.get("change_type") == "delete"
            and isinstance(change.get("relative_path"), str)
        ]
        if not deleted:
            return False
        delete_count = len(deleted)
        deleted_bytes = sum(
            baseline.get(str(change["relative_path"]), (0, ""))[0]
            for change in deleted
        )
        return requires_master_for_destructive_delete(
            delete_count=delete_count,
            deleted_bytes=deleted_bytes,
            baseline_file_count=len(baseline),
        )

    def _workspace_ignore_parts(
        self,
        working_dir: Path,
        base_ignore_parts: frozenset[str],
    ) -> frozenset[str]:
        """Merge safe workspace-local staging exclusions from `.niraiignore`.

        Patterns intentionally match one file/directory name at any depth, using
        the same fnmatch semantics as built-in Cursor staging exclusions. A
        workspace-local ignore can only reduce what reaches the isolated staging
        copy; it cannot grant access outside the Task root.
        """
        ignore_file = working_dir / CURSOR_WORKSPACE_IGNORE_FILE
        try:
            text = ignore_file.read_text(encoding="utf-8")
        except FileNotFoundError:
            return base_ignore_parts
        except (OSError, UnicodeError) as exc:
            raise AgentRuntimeError(
                f"Cursor staging could not read {CURSOR_WORKSPACE_IGNORE_FILE}"
            ) from exc

        ignored = set(base_ignore_parts)
        local_count = 0
        for raw_line in text.splitlines():
            pattern = raw_line.strip()
            if not pattern or pattern.startswith("#"):
                continue
            if len(pattern) > CURSOR_WORKSPACE_IGNORE_MAX_PATTERN_LENGTH:
                raise AgentRuntimeError(
                    f"{CURSOR_WORKSPACE_IGNORE_FILE} pattern exceeds "
                    f"{CURSOR_WORKSPACE_IGNORE_MAX_PATTERN_LENGTH} characters"
                )
            if "/" in pattern or "\\" in pattern or pattern in {".", ".."}:
                raise AgentRuntimeError(
                    f"{CURSOR_WORKSPACE_IGNORE_FILE} supports basename patterns only"
                )
            local_count += 1
            if local_count > CURSOR_WORKSPACE_IGNORE_MAX_PATTERNS:
                raise AgentRuntimeError(
                    f"{CURSOR_WORKSPACE_IGNORE_FILE} exceeds "
                    f"{CURSOR_WORKSPACE_IGNORE_MAX_PATTERNS} patterns"
                )
            ignored.add(pattern)
        return frozenset(ignored)

    def _read_only_staging_ignore_parts(self, working_dir: Path) -> frozenset[str]:
        ignored = set(CURSOR_READ_ONLY_IGNORE_NAMES)
        if working_dir.resolve() == self.root:
            ignored.update(CURSOR_NIRAI_REVIEW_IGNORE_NAMES)
        return self._workspace_ignore_parts(working_dir, frozenset(ignored))

    def _verify_read_only_review_unchanged(
        self,
        working_dir: Path,
        staging_dir: Path,
        baseline: dict[str, tuple[int, str]],
        *,
        ignore_parts: frozenset[str],
    ) -> None:
        staged = self._workspace_snapshot(staging_dir, ignore_parts=ignore_parts)
        changed_paths = self._changed_staged_paths(baseline, staged)
        if changed_paths:
            raise AgentRuntimeError(
                "Cursor read-only review attempted to modify its isolated staging copy; "
                "the review result was discarded"
            )
        current = self._workspace_snapshot(working_dir, ignore_parts=ignore_parts)
        if current != baseline:
            raise AgentRuntimeError(
                "Review target changed while Cursor was reviewing; rerun the review on the latest files"
            )

    def _staging_root_for(self, working_dir: Path, *, read_only: bool) -> Path:
        if read_only and working_dir.resolve() == self.root:
            root_digest = hashlib.sha256(
                str(self.root).casefold().encode("utf-8")
            ).hexdigest()[:24]
            return (
                Path(tempfile.gettempdir())
                / "nirai-cursor-staging"
                / root_digest
            ).resolve()
        return self.workspace_policy.default_workspace_root.resolve()

    def _prepare_staging_workspace(
        self,
        agent_session_id: str,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
        stable_key: str | None = None,
        staging_root: Path | None = None,
    ) -> tuple[Path, dict[str, tuple[int, str]]]:
        staging_root = (
            staging_root.resolve()
            if staging_root is not None
            else self.workspace_policy.default_workspace_root.resolve()
        )
        staging_root.mkdir(parents=True, exist_ok=True)
        # Reservation begins before stale cleanup so a sibling start cannot
        # reap this session's staging directory while process creation is still
        # pending. The thread-safe ownership set is the cleanup authority;
        # `_preparing_ids` remains an event-loop diagnostic/state hint only.
        self._preparing_ids.add(agent_session_id)
        self._claim_runtime_id(agent_session_id)
        self._cleanup_stale_staging_workspaces(staging_root)
        if stable_key is not None:
            stable_digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:24]
            staging_name = f".cursor-conversation-{stable_digest}"
        else:
            staging_name = f".cursor-stage-{agent_session_id}"
        staging_dir = (staging_root / staging_name).resolve()
        if (
            staging_dir.parent != staging_root
            or not staging_dir.name.startswith((".cursor-stage-", ".cursor-conversation-"))
        ):
            raise AgentSafetyError("Cursor staging workspace escaped Nirai internal staging root")
        self._cleanup_staging_workspace(staging_dir)
        # Validate links with a metadata-only walk, then calculate the baseline
        # digest from the same byte stream that creates staging. The old path
        # hashed every source file and then read every source file again through
        # copytree, doubling content I/O before Cursor could start.
        self._assert_workspace_has_no_links(working_dir, ignore_parts=ignore_parts)
        if stable_key is not None:
            # Stable Conversation staging has no Agent Session id in its path.
            # Own the directory name itself only once source validation passed,
            # so age-based cleanup cannot reap a long-running live turn and a
            # validation failure cannot leave a phantom ownership claim.
            self._claim_runtime_id(staging_name)
        try:
            baseline = self._copy_workspace_with_snapshot(
                working_dir,
                staging_dir,
                ignore_parts=ignore_parts,
            )
        except OSError as exc:
            self._cleanup_staging_workspace(staging_dir)
            raise AgentRuntimeUnavailableError("Cursor staging workspace could not be prepared") from exc
        except AgentRuntimeError:
            self._cleanup_staging_workspace(staging_dir)
            raise
        return staging_dir, baseline

    def _cleanup_stale_staging_workspaces(self, staging_root: Path) -> None:
        stage_prefix = ".cursor-stage-"
        conversation_prefix = ".cursor-conversation-"
        owned_ids = self._runtime_owned_snapshot()
        for child in staging_root.iterdir():
            if child.name.startswith(stage_prefix):
                owner_key = child.name[len(stage_prefix):]
            elif child.name.startswith(conversation_prefix):
                owner_key = child.name
            else:
                continue
            if owner_key in owned_ids or not self._runtime_path_is_stale(child):
                continue
            self._cleanup_staging_workspace(child)

    @staticmethod
    def _runtime_path_is_stale(path: Path) -> bool:
        """Protect runtime state that may belong to another live Core process.

        In-process ownership is tracked explicitly, but another Core instance
        cannot share that set. A young directory is therefore never reaped as
        stale; abandoned state becomes eligible only after a conservative age.
        """
        try:
            return time.time() - path.stat().st_mtime >= CURSOR_STALE_RUNTIME_AGE_SEC
        except OSError:
            return False

    @classmethod
    def _iter_workspace_files(
        cls,
        root: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ):
        """Yield workspace files while pruning ignored directory trees early.

        ``Path.rglob`` still descends into ignored trees before callers can
        discard their results. For Nirai-root review that made ``runtime`` and
        ``node_modules`` expensive even though their contents were excluded.
        ``os.walk(topdown=True)`` lets us remove ignored directories before
        descent and validate every traversed symlink/junction in the same pass.
        """
        resolved_root = root.resolve()
        ignored = {part.casefold() for part in ignore_parts}

        def is_ignored(name: str) -> bool:
            folded = name.casefold()
            return any(fnmatch.fnmatchcase(folded, pattern) for pattern in ignored)
        for current_raw, dirnames, filenames in os.walk(
            resolved_root,
            topdown=True,
            followlinks=False,
        ):
            current = Path(current_raw)
            kept_dirs: list[str] = []
            for name in sorted(dirnames, key=str.casefold):
                if is_ignored(name):
                    continue
                child = current / name
                relative = child.relative_to(resolved_root)
                is_junction = getattr(child, "is_junction", lambda: False)
                if child.is_symlink() or is_junction():
                    raise AgentRuntimeError(
                        f"Cursor staging refuses linked workspace entries: {relative.as_posix()}"
                    )
                kept_dirs.append(name)
            dirnames[:] = kept_dirs

            for name in sorted(filenames, key=str.casefold):
                if is_ignored(name):
                    continue
                path = current / name
                relative = path.relative_to(resolved_root)
                is_junction = getattr(path, "is_junction", lambda: False)
                if path.is_symlink() or is_junction():
                    raise AgentRuntimeError(
                        f"Cursor staging refuses linked workspace entries: {relative.as_posix()}"
                    )
                yield path

    @classmethod
    def _assert_workspace_has_no_links(
        cls,
        working_dir: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ) -> None:
        # Iteration itself validates every traversed directory/file link.
        for _path in cls._iter_workspace_files(working_dir, ignore_parts=ignore_parts):
            pass

    @classmethod
    def _copy_workspace_with_snapshot(
        cls,
        source_root: Path,
        staging_dir: Path,
        *,
        ignore_parts: frozenset[str],
    ) -> dict[str, tuple[int, str]]:
        resolved_root = source_root.resolve()
        snapshot: dict[str, tuple[int, str]] = {}
        total_bytes = 0

        def copy_and_hash(source_raw: str, target_raw: str) -> str:
            nonlocal total_bytes
            source = Path(source_raw)
            target = Path(target_raw)
            relative = source.resolve().relative_to(resolved_root)
            if len(snapshot) >= CURSOR_STAGE_FILE_LIMIT:
                raise AgentRuntimeError(
                    f"Cursor staging file limit exceeded ({CURSOR_STAGE_FILE_LIMIT})"
                )
            digest = hashlib.sha256()
            copied_bytes = 0
            with source.open("rb") as source_handle, target.open("wb") as target_handle:
                for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                    copied_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > CURSOR_STAGE_BYTE_LIMIT:
                        raise AgentRuntimeError(
                            f"Cursor staging byte limit exceeded ({CURSOR_STAGE_BYTE_LIMIT})"
                        )
                    digest.update(chunk)
                    target_handle.write(chunk)
            shutil.copystat(source, target, follow_symlinks=False)
            snapshot[relative.as_posix()] = (copied_bytes, digest.hexdigest())
            return str(target)

        shutil.copytree(
            resolved_root,
            staging_dir,
            copy_function=copy_and_hash,
            ignore=shutil.ignore_patterns(*sorted(ignore_parts)),
        )
        return snapshot

    @classmethod
    def _workspace_snapshot(
        cls,
        root: Path,
        *,
        ignore_parts: frozenset[str] = frozenset({".cursor", ".git"}),
    ) -> dict[str, tuple[int, str]]:
        resolved_root = root.resolve()
        snapshot: dict[str, tuple[int, str]] = {}
        total_bytes = 0
        for path in cls._iter_workspace_files(resolved_root, ignore_parts=ignore_parts):
            if not path.is_file():
                continue
            relative = path.relative_to(resolved_root)
            size = path.stat().st_size
            total_bytes += size
            if len(snapshot) >= CURSOR_STAGE_FILE_LIMIT:
                raise AgentRuntimeError(
                    f"Cursor staging file limit exceeded ({CURSOR_STAGE_FILE_LIMIT})"
                )
            if total_bytes > CURSOR_STAGE_BYTE_LIMIT:
                raise AgentRuntimeError(
                    f"Cursor staging byte limit exceeded ({CURSOR_STAGE_BYTE_LIMIT})"
                )
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            snapshot[relative.as_posix()] = (size, digest.hexdigest())
        return snapshot

    def _collect_staged_changes(
        self,
        working_dir: Path,
        staging_dir: Path,
        baseline: dict[str, tuple[int, str]],
    ) -> list[dict[str, Any]]:
        staged = self._workspace_snapshot(staging_dir)
        changed_paths = self._changed_staged_paths(baseline, staged)
        self._validate_changed_staged_paths(changed_paths)
        return self._build_staged_change_manifest(
            working_dir,
            staging_dir,
            baseline,
            staged,
            changed_paths,
        )

    def _freeze_staged_changes(
        self,
        working_dir: Path,
        staging_dir: Path,
        review_dir: Path,
        baseline: dict[str, tuple[int, str]],
        *,
        ignore_parts: frozenset[str] = CURSOR_WRITABLE_IGNORE_NAMES,
    ) -> tuple[list[dict[str, Any]], dict[str, tuple[int, str]], dict[str, tuple[int, str]]]:
        staged_before = self._workspace_snapshot(staging_dir, ignore_parts=ignore_parts)
        changed_paths = self._changed_staged_paths(baseline, staged_before)
        self._validate_changed_staged_paths(changed_paths)
        if not changed_paths:
            return [], staged_before, {}

        self._cleanup_staging_workspace(review_dir)
        review_dir.mkdir(parents=True, exist_ok=False)
        try:
            for relative in changed_paths:
                if relative not in staged_before:
                    continue
                source = staging_dir / Path(relative)
                if not source.is_file():
                    raise AgentRuntimeError(
                        f"Cursor staged source disappeared during review freeze: {relative}"
                    )
                target = review_dir / Path(relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)

            staged_after = self._workspace_snapshot(staging_dir, ignore_parts=ignore_parts)
            if staged_after != staged_before:
                raise AgentRuntimeError(
                    "Cursor staging workspace changed while the review snapshot was being frozen"
                )
            reviewed_bundle = self._workspace_snapshot(review_dir, ignore_parts=ignore_parts)
            changes = self._build_staged_change_manifest(
                working_dir,
                review_dir,
                baseline,
                staged_before,
                changed_paths,
            )
            return changes, staged_before, reviewed_bundle
        except Exception:
            self._cleanup_staging_workspace(review_dir)
            raise

    @staticmethod
    def _changed_staged_paths(
        baseline: dict[str, tuple[int, str]],
        staged: dict[str, tuple[int, str]],
    ) -> list[str]:
        return sorted(
            {
                relative
                for relative in set(baseline) | set(staged)
                if baseline.get(relative) != staged.get(relative)
            },
            key=str.casefold,
        )

    @staticmethod
    def _validate_changed_staged_paths(changed_paths: list[str]) -> None:
        if "task.md" in changed_paths:
            raise AgentRuntimeError("Cursor attempted to modify protected Task metadata: task.md")
        if len(changed_paths) > 50:
            raise AgentRuntimeError(
                "Cursor produced more than 50 file changes; split the Task so every change can be reviewed safely"
            )

    @staticmethod
    def _build_staged_change_manifest(
        working_dir: Path,
        source_dir: Path,
        baseline: dict[str, tuple[int, str]],
        staged: dict[str, tuple[int, str]],
        changed_paths: list[str],
    ) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for relative in changed_paths:
            original = working_dir / Path(relative)
            staged_path = source_dir / Path(relative)
            if relative not in baseline:
                change_type = "create"
            elif relative not in staged:
                change_type = "delete"
            else:
                change_type = "modify"
            payload: dict[str, Any] = {
                "path": str(original),
                "relative_path": relative,
                "change_type": change_type,
            }
            diff = _cursor_file_diff(original, staged_path, relative, change_type)
            if diff is not None:
                payload["diff"] = diff
            changes.append(payload)
        return changes

    def _apply_staged_changes(
        self,
        working_dir: Path,
        staged_source_dir: Path,
        baseline: dict[str, tuple[int, str]],
        changes: list[dict[str, Any]],
        *,
        ignore_parts: frozenset[str] = CURSOR_WRITABLE_IGNORE_NAMES,
    ) -> None:
        current = self._workspace_snapshot(working_dir, ignore_parts=ignore_parts)
        if current != baseline:
            raise AgentRuntimeError(
                "Task workspace changed while Cursor was working; staged changes were not applied"
            )

        recovery_root = (self.root / "runtime" / "cursor_recovery").resolve()
        try:
            recovery_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            # Recovery storage must exist before the first real workspace write.
            # If it cannot be prepared, fail without touching user files.
            raise AgentRuntimeError("Cursor recovery storage could not be prepared") from exc
        rollback_root = (recovery_root / f".RB-{uuid4()}").resolve()
        try:
            rollback_root.relative_to(recovery_root)
        except ValueError as exc:
            raise AgentRuntimeError("Cursor rollback backup path escaped recovery storage") from exc
        rollback_root.mkdir(parents=True, exist_ok=False)
        normalized: list[tuple[str, str, Path, Path | None]] = []
        preserve_rollback_root = False
        try:
            # Capture every original before the first real write. If any backup
            # fails, the Task workspace is still untouched.
            for change in changes:
                relative = change.get("relative_path")
                change_type = change.get("change_type")
                if not isinstance(relative, str) or change_type not in {"create", "modify", "delete"}:
                    raise AgentRuntimeError("Cursor staged change metadata is invalid")
                target = self.workspace_policy.assert_write_path(
                    Path(relative),
                    working_dir=working_dir,
                )
                backup: Path | None = None
                if change_type in {"modify", "delete"}:
                    if not target.is_file():
                        raise AgentRuntimeError(
                            f"Cursor staged target disappeared before apply: {relative}"
                        )
                    backup = rollback_root / Path(relative)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    self._durable_copy_file(target, backup)
                normalized.append((relative, change_type, target, backup))

            recovery_manifest: dict[str, Any] = {
                "version": 1,
                "state": "applying",
                "working_dir": str(working_dir),
                "staged_source_dir": str(staged_source_dir),
                "created_at_unix": time.time(),
                "changes": [
                    {
                        "relative_path": relative,
                        "change_type": change_type,
                        "backup_relative_path": relative if backup is not None else None,
                    }
                    for relative, change_type, _target, backup in normalized
                ],
            }
            # Hard-crash recovery evidence must exist before the first real
            # workspace write. Master may choose whether to restore it later;
            # Nirai does not auto-rollback a tree that may have changed again.
            self._write_recovery_manifest(rollback_root, recovery_manifest)

            try:
                for relative, change_type, target, _backup in normalized:
                    if change_type == "delete":
                        target.unlink()
                        continue
                    source = staged_source_dir / Path(relative)
                    if not source.is_file():
                        raise AgentRuntimeError(f"Cursor staged source is missing: {relative}")
                    target = self.workspace_policy.prepare_write_path(
                        target,
                        working_dir=working_dir,
                    )
                    self._atomic_copy_file(source, target)
            except Exception as apply_error:
                rollback_errors: list[str] = []
                for relative, change_type, target, backup in reversed(normalized):
                    try:
                        if change_type == "create":
                            target.unlink(missing_ok=True)
                        else:
                            if backup is None or not backup.is_file():
                                raise OSError("rollback backup is missing")
                            self._atomic_copy_file(backup, target)
                    except Exception as rollback_error:
                        rollback_errors.append(
                            f"{relative}: {type(rollback_error).__name__}: {_bounded_text(rollback_error, 200)}"
                        )
                if rollback_errors:
                    recovery_dir = recovery_root / f"REC-{uuid4()}"
                    try:
                        # Rollback data already lives outside the Cursor home.
                        # Publish it atomically only after the manifest is durable.
                        manifest = {
                            **recovery_manifest,
                            "state": "rollback_incomplete",
                            "rollback_errors": rollback_errors,
                            "apply_error": f"{type(apply_error).__name__}: {_bounded_text(apply_error, 500)}",
                        }
                        self._write_recovery_manifest(rollback_root, manifest)
                        os.replace(rollback_root, recovery_dir)
                    except OSError as recovery_error:
                        # The rollback tree is already outside normal Cursor-home
                        # cleanup. Leave it untouched for manual recovery.
                        preserve_rollback_root = True
                        raise AgentRuntimeError(
                            "Cursor staged apply failed, rollback was incomplete, and recovery backup "
                            f"could not be published; original backup remains at "
                            f"{rollback_root}: {_bounded_text(recovery_error, 300)}"
                        ) from apply_error
                    raise AgentRuntimeError(
                        "Cursor staged apply failed and rollback was incomplete; recovery backup preserved at "
                        f"{recovery_dir}: " + "; ".join(rollback_errors[:5])
                    ) from apply_error
                raise AgentRuntimeError(
                    f"Cursor staged apply failed and was rolled back: {_bounded_text(apply_error, 500)}"
                ) from apply_error
        finally:
            if not preserve_rollback_root:
                try:
                    self._cleanup_staging_workspace(rollback_root)
                except AgentRuntimeError:
                    LOGGER.warning("cursor_rollback_backup_cleanup_failed", exc_info=True)

    @staticmethod
    def _write_recovery_manifest(rollback_root: Path, payload: dict[str, Any]) -> None:
        manifest_path = rollback_root / "recovery.json"
        temporary = rollback_root / ".recovery.json.tmp"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, manifest_path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("cursor_recovery_manifest_temp_cleanup_failed", exc_info=True)

    @staticmethod
    def _durable_copy_file(source: Path, target: Path) -> None:
        shutil.copy2(source, target)
        with target.open("r+b") as handle:
            handle.flush()
            os.fsync(handle.fileno())

    @staticmethod
    def _atomic_copy_file(source: Path, target: Path) -> None:
        if not target.parent.is_dir():
            raise AgentRuntimeError("Cursor apply target parent directory disappeared before write")
        temp = target.with_name(f".{target.name}.nirai-cursor-apply.tmp")
        try:
            shutil.copy2(source, temp)
            os.replace(temp, target)
        finally:
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                LOGGER.warning("cursor_apply_temp_cleanup_failed path=%s", temp, exc_info=True)

    def _cleanup_staging_workspace(self, staging_dir: Path) -> None:
        last_error: OSError | None = None
        try:
            for _ in range(CURSOR_STAGE_CLEANUP_RETRIES):
                try:
                    shutil.rmtree(staging_dir, ignore_errors=False)
                    if not staging_dir.exists():
                        return
                except FileNotFoundError:
                    return
                except OSError as exc:
                    last_error = exc
            if staging_dir.exists():
                raise AgentRuntimeError(
                    f"Cursor staging workspace cleanup failed: {staging_dir.name}: {_bounded_text(last_error, 300)}"
                )
        finally:
            # Conversation staging is owned by its stable directory name rather
            # than by one transient Agent Session id.
            self._release_runtime_id(staging_dir.name)


def _cursor_review_manifest(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def payload_size(items: list[dict[str, Any]]) -> int:
        return len(json.dumps({"changes": items}, ensure_ascii=False, separators=(",", ":")))

    # Leave ample room below AgentRuntimeManager's 32k per-event budget for the
    # event envelope, operation id, and state metadata. Every changed path must
    # remain visible in the approval context. Diffs are optional; hidden paths
    # are not.
    review = [dict(change) for change in changes]
    if payload_size(review) <= 24_000:
        return review
    review = [
        {key: value for key, value in change.items() if key != "diff"}
        for change in changes
    ]
    if payload_size(review) <= 24_000:
        return review
    raise AgentRuntimeError(
        "Cursor staged change manifest is too large to review safely in one approval; split the Task"
    )


def _cursor_file_diff(
    original: Path,
    staged: Path,
    relative: str,
    change_type: str,
) -> str | None:
    old_text = _read_cursor_diff_text(original) if change_type != "create" else ""
    new_text = _read_cursor_diff_text(staged) if change_type != "delete" else ""
    if old_text is None or new_text is None:
        return None
    diff = "\n".join(difflib.unified_diff(
        old_text.splitlines(),
        new_text.splitlines(),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="",
    ))
    if len(diff) > 12_000:
        return diff[:11_999].rstrip() + "…"
    return diff


def _read_cursor_diff_text(path: Path) -> str | None:
    if not path.is_file():
        return ""
    try:
        if path.stat().st_size > CURSOR_DIFF_TEXT_FILE_LIMIT:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
