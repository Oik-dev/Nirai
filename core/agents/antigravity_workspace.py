"""Antigravity's local workspace bridge, including the Master approval boundary."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import difflib
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .base import AgentRunRequest, AgentRuntimeError, AgentRuntimeProtocolError, EmitEvent, WaitForMaster
from .safety import AgentSafetyError, AgentWorkspacePolicy

ANTIGRAVITY_FILE_TEXT_LIMIT = 512 * 1024
ANTIGRAVITY_READ_OUTPUT_LIMIT = 24_000
ANTIGRAVITY_DIFF_LIMIT = 24_000
ANTIGRAVITY_LIST_ENTRY_LIMIT = 500
ANTIGRAVITY_LIST_OUTPUT_LIMIT = 24_000


@dataclass(frozen=True)
class _FileFingerprint:
    exists: bool
    digest: str | None
    size: int


class AntigravityWorkspaceMixin:
    """Local file operations using the Adapter's existing workspace policy."""

    workspace_policy: AgentWorkspacePolicy

    def _list_files(self, request: AgentRunRequest, arguments: dict[str, Any]) -> dict[str, Any]:
        directory = _resolve_relative_path(
            request.working_dir,
            arguments.get("path", "."),
            must_exist=True,
        )
        if not directory.is_dir():
            raise AgentRuntimeError("nirai_list_files target is not a directory")
        entries: list[dict[str, Any]] = []
        for child in sorted(directory.iterdir(), key=lambda value: value.name.casefold()):
            if len(entries) >= ANTIGRAVITY_LIST_ENTRY_LIMIT:
                break
            resolved = child.resolve()
            if not _is_within(resolved, request.working_dir.resolve()):
                entry: dict[str, Any] = {"name": child.name, "type": "blocked_link"}
            else:
                entry = {
                    "name": child.name,
                    "type": "directory" if child.is_dir() else "file",
                    **({"size": child.stat().st_size} if child.is_file() else {}),
                }
            projected_chars = len(json.dumps(entries, ensure_ascii=False)) + len(json.dumps(entry, ensure_ascii=False))
            if projected_chars > ANTIGRAVITY_LIST_OUTPUT_LIMIT:
                break
            entries.append(entry)
        return {
            "ok": True,
            "path": _relative_display(directory, request.working_dir),
            "entries": entries,
            "truncated": len(entries) >= ANTIGRAVITY_LIST_ENTRY_LIMIT,
        }

    def _read_text_file(self, request: AgentRunRequest, arguments: dict[str, Any]) -> dict[str, Any]:
        target = _resolve_relative_path(request.working_dir, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_read_text_file target is not a file")
        if target.stat().st_size > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_read_text_file refuses files larger than 512 KiB")
        text = target.read_text(encoding="utf-8")
        start_line = _bounded_int(arguments.get("start_line"), default=1, minimum=1, maximum=1_000_000)
        max_lines = _bounded_int(arguments.get("max_lines"), default=200, minimum=1, maximum=500)
        lines = text.splitlines()
        selected = lines[start_line - 1 : start_line - 1 + max_lines]
        content = "\n".join(selected)
        if len(content) > ANTIGRAVITY_READ_OUTPUT_LIMIT:
            content = content[: ANTIGRAVITY_READ_OUTPUT_LIMIT - 1] + "…"
        return {
            "ok": True,
            "path": _relative_display(target, request.working_dir),
            "start_line": start_line,
            "content": content,
            "has_more": start_line - 1 + max_lines < len(lines),
        }

    async def _write_text_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"))
        content = arguments.get("content")
        if not isinstance(content, str):
            raise AgentRuntimeProtocolError("nirai_write_text_file content must be a string")
        if len(content.encode("utf-8")) > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_write_text_file refuses content larger than 512 KiB")
        before_text = _read_small_text(target)
        if target.exists() and before_text is None:
            raise AgentRuntimeError(
                "nirai_write_text_file refuses to replace an existing non-UTF-8 or >512 KiB file because Master cannot review the complete old text"
            )
        before = _fingerprint(target)
        diff = _review_diff(before_text or "", content, _relative_display(target, request.working_dir))
        await self._approve_file_change(
            request,
            call_id,
            target,
            "modify" if before.exists else "create",
            diff,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        target = await self._commit_approved_text(
            request, call_id, target, content,
            change_type="modify" if before.exists else "create",
            emit=emit,
        )
        return {"ok": True, "path": _relative_display(target, request.working_dir)}

    async def _edit_text_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_edit_text_file target is not a file")
        before_text = _read_small_text(target)
        if before_text is None:
            raise AgentRuntimeError("nirai_edit_text_file requires a UTF-8 text file <= 512 KiB")
        old_text = arguments.get("old_text")
        new_text = arguments.get("new_text")
        replace_all = arguments.get("replace_all") is True
        if not isinstance(old_text, str) or not old_text:
            raise AgentRuntimeProtocolError("nirai_edit_text_file old_text must be a non-empty string")
        if not isinstance(new_text, str):
            raise AgentRuntimeProtocolError("nirai_edit_text_file new_text must be a string")
        count = before_text.count(old_text)
        if count == 0:
            raise AgentRuntimeError("nirai_edit_text_file old_text was not found")
        if not replace_all and count != 1:
            raise AgentRuntimeError(
                "nirai_edit_text_file old_text is ambiguous; provide more context or replace_all=true"
            )
        after_text = before_text.replace(old_text, new_text) if replace_all else before_text.replace(old_text, new_text, 1)
        if len(after_text.encode("utf-8")) > ANTIGRAVITY_FILE_TEXT_LIMIT:
            raise AgentRuntimeError("nirai_edit_text_file result would exceed 512 KiB")
        before = _fingerprint(target)
        diff = _review_diff(before_text, after_text, _relative_display(target, request.working_dir))
        await self._approve_file_change(
            request,
            call_id,
            target,
            "modify",
            diff,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
        )
        target = await self._commit_approved_text(
            request, call_id, target, after_text, change_type="modify", emit=emit,
        )
        return {"ok": True, "path": _relative_display(target, request.working_dir), "replacements": count if replace_all else 1}

    async def _commit_approved_text(
        self,
        request: AgentRunRequest,
        call_id: str,
        target: Path,
        content: str,
        *,
        change_type: str,
        emit: EmitEvent,
    ) -> Path:
        """Apply only after the caller has approved and revalidated the change."""
        target = self.workspace_policy.prepare_write_path(
            target,
            working_dir=request.working_dir,
        )
        _atomic_write_text(target, content)
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "completed",
            "changes": [{
                "relative_path": _relative_display(target, request.working_dir),
                "change_type": change_type,
            }],
        })
        return target

    async def _delete_file(
        self,
        request: AgentRunRequest,
        call_id: str,
        arguments: dict[str, Any],
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> dict[str, Any]:
        target = _resolve_write_target(request, arguments.get("path"), must_exist=True)
        if not target.is_file():
            raise AgentRuntimeError("nirai_delete_file only deletes files")
        before = _fingerprint(target)
        relative = _relative_display(target, request.working_dir)
        await self._approve_file_change(
            request,
            call_id,
            target,
            "delete",
            None,
            before,
            emit=emit,
            wait_for_master=wait_for_master,
            description=f"Delete {relative} ({before.size} bytes, sha256={before.digest})",
        )
        target.unlink()
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "completed",
            "status": "completed",
            "changes": [{"relative_path": relative, "change_type": "delete"}],
        })
        return {"ok": True, "path": relative}

    async def _approve_file_change(
        self,
        request: AgentRunRequest,
        call_id: str,
        target: Path,
        change_type: str,
        diff: str | None,
        baseline: _FileFingerprint,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
        description: str | None = None,
    ) -> None:
        relative = _relative_display(target, request.working_dir)
        approved_canonical_path = target.resolve()
        change: dict[str, Any] = {
            "path": str(target),
            "relative_path": relative,
            "change_type": change_type,
        }
        if diff:
            change["diff"] = diff
        await emit("file_change", {
            "operation_id": call_id,
            "phase": "proposed",
            "status": "pending_approval",
            "changes": [change],
        })
        payload = {
            "request_id": call_id,
            "operation_id": call_id,
            "kind": "file_change",
            "title": f"Antigravity wants to {change_type} {relative}",
            "description": description or "Review the exact local Task workspace change",
            "grant_root": str(request.working_dir),
            "options": ["approve_once", "reject", "cancel"],
        }
        await emit("approval_request", payload)
        response = await wait_for_master(call_id, "approval", payload)
        decision = response.get("decision") if isinstance(response, dict) else None
        if decision == "cancel":
            raise asyncio.CancelledError
        if decision != "approve_once":
            raise AgentRuntimeError("Master rejected the Antigravity local file change")
        current_canonical_path = target.resolve()
        if current_canonical_path != approved_canonical_path:
            raise AgentRuntimeError(
                "Task workspace path topology changed while Antigravity approval was pending; local change was not applied"
            )
        self.workspace_policy.assert_write_path(current_canonical_path, working_dir=request.working_dir)
        if _fingerprint(current_canonical_path) != baseline:
            raise AgentRuntimeError(
                "Task workspace file changed while Antigravity approval was pending; local change was not applied"
            )


def _resolve_relative_path(working_dir: Path, raw_value: object, *, must_exist: bool) -> Path:
    if raw_value is None:
        raw_value = "."
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise AgentSafetyError("Antigravity local path must be a non-empty relative path")
    raw = Path(raw_value.strip())
    if raw.is_absolute():
        raise AgentSafetyError("Antigravity local paths must be relative to the Task workspace")
    resolved_working = working_dir.resolve()
    candidate = (resolved_working / raw).resolve()
    if not _is_within(candidate, resolved_working):
        raise AgentSafetyError("Antigravity local path escaped the Task workspace")
    if must_exist and not candidate.exists():
        raise AgentRuntimeError(f"Local Task workspace path does not exist: {raw_value}")
    return candidate


def _resolve_write_target(
    request: AgentRunRequest,
    raw_value: object,
    *,
    must_exist: bool = False,
) -> Path:
    target = _resolve_relative_path(request.working_dir, raw_value, must_exist=must_exist)
    target = request.working_dir.resolve() / target.relative_to(request.working_dir.resolve())
    target = target.resolve()
    relative = target.relative_to(request.working_dir.resolve()).as_posix()
    if relative.casefold() == "task.md":
        raise AgentSafetyError("Antigravity may not modify protected Task metadata: task.md")
    request_path = Path(relative)
    return request.working_dir.resolve() / request_path


def _fingerprint(path: Path) -> _FileFingerprint:
    if not path.exists():
        return _FileFingerprint(False, None, 0)
    if not path.is_file():
        raise AgentRuntimeError("Antigravity local file operation targeted a non-file")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(64 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return _FileFingerprint(True, digest.hexdigest(), size)


def _read_small_text(path: Path) -> str | None:
    if not path.exists():
        return ""
    if not path.is_file() or path.stat().st_size > ANTIGRAVITY_FILE_TEXT_LIMIT:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _review_diff(before: str, after: str, relative: str) -> str:
    diff = "\n".join(difflib.unified_diff(
        before.splitlines(),
        after.splitlines(),
        fromfile=f"a/{relative}",
        tofile=f"b/{relative}",
        lineterm="",
    ))
    if len(diff) > ANTIGRAVITY_DIFF_LIMIT:
        raise AgentRuntimeError(
            "Antigravity local file change diff exceeds the safe Master-review limit; split the change into smaller edits"
        )
    return diff


def _atomic_write_text(target: Path, content: str) -> None:
    if not target.parent.is_dir():
        raise AgentRuntimeError("Antigravity local write parent directory disappeared before apply")
    temp = target.with_name(f".{target.name}.nirai-{uuid4().hex}.tmp")
    try:
        temp.write_text(content, encoding="utf-8")
        os.replace(temp, target)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def _relative_display(path: Path, working_dir: Path) -> str:
    return path.resolve().relative_to(working_dir.resolve()).as_posix() or "."


def _is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
        return True
    except ValueError:
        return False


def _bounded_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return min(max(value, minimum), maximum)
