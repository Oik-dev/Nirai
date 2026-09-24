"""Single Core owner for Workflow admission and completion.

The lease stores membership and explicit resolutions, never copies Task state.
Task execution remains owned by Queue/Agent records. All clients use this service;
the JSON lease remains readable by World's passive watchdog.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
from typing import Awaitable, Callable
from urllib.parse import urlsplit
from uuid import uuid4

from ..brains.process_manager import ProcessManager
from ..brains.base import BrainError
from ..atomic_json import atomic_json


class WorkflowError(ValueError):
    pass


def conversation_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        url = urlsplit(value)
        match = re.search(r"(?:^|/)c/([^/]+)", url.path)
        if url.scheme == "https" and url.hostname == "chatgpt.com" and match:
            return match[1].removeprefix("WEB:") or None
    except ValueError:
        pass
    return None


def revision(previous: str | None = None) -> str:
    now = datetime.now(timezone.utc)
    if previous:
        now = max(now, datetime.fromisoformat(previous.replace("Z", "+00:00")) + timedelta(milliseconds=1))
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class HoloWorkflow:
    def __init__(self, root: Path, inspect_task: Callable[[str], dict],
                 build_status: Callable[[], Awaitable[dict]] | None = None,
                 stop_task: Callable[[str], Awaitable[None]] | None = None) -> None:
        self.root = root
        self.path = root / "runtime/holo/workflow.json"
        self.lock = asyncio.Lock()
        self.inspect_task = inspect_task
        self.build_status = build_status or self._build_status
        self.stop_task = stop_task

    async def _build_status(self) -> dict:
        node = shutil.which("node")
        if not node:
            raise WorkflowError("Node.js is required to verify World build inputs")
        try:
            result = await ProcessManager().run(f"workflow-build-{uuid4()}",
                (node, str(self.root / "tools/world-build-state.mjs"), "status"),
                cwd=self.root, timeout_sec=20)
        except BrainError as exc:
            raise WorkflowError(f"World build verification unavailable: {exc}") from exc
        if result.returncode:
            raise WorkflowError(f"World build verification failed: {result.stderr[:1000]}")
        status = json.loads(result.stdout)
        if not isinstance(status, dict) or not isinstance(status.get("fingerprint"), str):
            raise WorkflowError("Invalid World build fingerprint")
        return status

    def read(self) -> dict | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        if not isinstance(value, dict) or value.get("version") != 1:
            raise WorkflowError("Invalid Workflow record")
        if (not isinstance(value.get("workflow_id"), str) or not value["workflow_id"]
                or not isinstance(value.get("dive_session_id"), str) or not value["dive_session_id"]
                or value.get("state") not in {"active", "completed"}
                or not conversation_id(value.get("conversation_url"))):
            raise WorkflowError("Invalid Workflow identity")
        for key in ("started_at", "updated_at"):
            if not isinstance(value.get(key), str):
                raise WorkflowError("Invalid Workflow timestamp")
            timestamp = datetime.fromisoformat(value[key].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise WorkflowError("Workflow timestamps must include timezone")
        if not isinstance(value.get("resolutions", {}), dict):
            raise WorkflowError("Invalid Workflow resolutions")
        for entry in value.get("resolutions", {}).values():
            if not isinstance(entry, dict) or entry.get("kind") not in {"abandoned", "superseded"}:
                raise WorkflowError("Invalid Workflow resolution entry")
            if entry["kind"] == "superseded" and entry.get("replacement_task_id") not in value.get("task_ids", []):
                raise WorkflowError("Invalid Workflow replacement Task")
        self._members(value)
        return value

    def _members(self, workflow: dict) -> list[str]:
        raw = workflow.get("task_ids", [])
        if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
            raise WorkflowError("Invalid Workflow Task membership")
        members = set(raw)
        # Upgrade only exact legacy owners, once on the next mutation. Never
        # infer membership from the current tab or from creation timestamps.
        if "task_ids" not in workflow:
            owner_root = self.root / "runtime/holo/task_owners"
            for path in owner_root.glob("*.json"):
                owner = json.loads(path.read_text(encoding="utf-8"))
                if owner.get("workflow_id") == workflow["workflow_id"]:
                    members.add(owner["task_id"])
        if any(not isinstance(item, str) or not re.fullmatch(r"(?:T|HR|IA)-[A-Za-z0-9-]+", item) for item in members):
            raise WorkflowError("Invalid Workflow Task membership")
        return sorted(members)

    def _save(self, workflow: dict) -> dict:
        updated = {**workflow, "task_ids": self._members(workflow),
                   "updated_at": revision(workflow.get("updated_at")), "writer": "core"}
        atomic_json(self.path, updated)
        return updated

    def require_active(self, workflow_id: str) -> dict:
        current = self.read()
        if not workflow_id or not current or current["workflow_id"] != workflow_id:
            raise WorkflowError("workflow_id does not match the current Workflow")
        if current["state"] != "active":
            raise WorkflowError("Workflow is already completed or cancelled; start a new Workflow")
        return current

    def admit(self, task_id: str, dive_id: str, url: str, expected: str | None = None) -> str | None:
        """Called inside lock before any Task launch/Queue mutation can await."""
        if expected is not None and (not isinstance(expected, str) or not expected.strip()):
            raise WorkflowError("workflow_id must be a non-empty string")
        current = self.read()
        if expected:
            current = self.require_active(expected)
        matches = current and current.get("dive_session_id") == dive_id and (
            conversation_id(current.get("conversation_url")) == conversation_id(url))
        if expected and not matches:
            raise WorkflowError("Task owner does not match the requested Workflow")
        if not matches or current["state"] != "active":
            return None
        self._save({**current, "task_ids": sorted(set(self._members(current)) | {task_id})})
        return current["workflow_id"]

    def forget_unstarted(self, task_id: str) -> None:
        current = self.read()
        if current and current["state"] == "active" and task_id in self._members(current):
            self._save({**current, "task_ids": [item for item in self._members(current) if item != task_id]})

    def require_recoverable(self, task_id: str, workflow_id: str | None) -> None:
        if not workflow_id:
            return
        current = self.require_active(workflow_id)
        if task_id in current.get("resolutions", {}):
            raise WorkflowError("Task was explicitly resolved; start new work instead of reviving it")

    def blockers(self, workflow: dict) -> list[dict]:
        blocked = []
        for task_id in self._members(workflow):
            task = self.inspect_task(task_id)
            resolution = workflow.get("resolutions", {}).get(task_id)
            state = task.get("state") or task.get("phase") or "unknown"
            if task.get("finalizing"):
                state = "finalizing"
            elif state in {"completed", "cancelled", "done"}:
                continue
            elif resolution and resolution.get("agent_session_id") == task.get("agent_session_id"):
                if resolution.get("kind") == "abandoned" and state in {"failed", "interrupted", "cancelled", "unknown"}:
                    continue
                if resolution.get("kind") == "superseded" and state in {"failed", "interrupted", "cancelled"}:
                    replacement = self.inspect_task(resolution["replacement_task_id"])
                    if replacement.get("state") == "completed" and not replacement.get("finalizing"):
                        continue
            blocked.append({"task_id": task_id, "state": state,
                            "agent_session_id": task.get("agent_session_id")})
        return blocked

    async def command(self, action: str, *, workflow_id: str | None = None,
                      dive_session_id: str | None = None, conversation_url: str | None = None,
                      label: str = "", task_id: str | None = None, resolution: str | None = None,
                      replacement_task_id: str | None = None, note: str = "") -> dict:
        for value in (action, label, note):
            if not isinstance(value, str):
                raise WorkflowError("Workflow action, label and note must be strings")
        for value in (workflow_id, dive_session_id, conversation_url, task_id, resolution, replacement_task_id):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise WorkflowError("Workflow identifiers must be non-empty strings")
        async with self.lock:
            current = self.read()
            if action == "status":
                if dive_session_id and current and current["dive_session_id"] != dive_session_id:
                    current = None
                return {"ok": True, "workflow": current,
                        "blockers": self.blockers(current) if current and current["state"] == "active" else []}
            if action == "start":
                if not label.strip() or len(label) > 1000 or not dive_session_id or not conversation_id(conversation_url):
                    raise WorkflowError("Workflow start requires a label and owning Dive Conversation")
                if current and current["state"] == "active":
                    if (current["dive_session_id"] != dive_session_id or
                        conversation_id(current["conversation_url"]) != conversation_id(conversation_url)):
                        raise WorkflowError("Another active Workflow belongs to a different Dive or Conversation")
                    return {"ok": True, "resumed": True, "workflow": self._save({**current, "label": label.strip()})}
                build = await self.build_status()
                now = revision()
                new = {"version": 1, "workflow_id": str(uuid4()), "state": "active", "label": label.strip(),
                       "dive_session_id": dive_session_id, "conversation_url": conversation_url,
                       "started_at": now, "updated_at": now, "task_ids": [], "resolutions": {},
                       "world_build_fingerprint_at_start": build["fingerprint"]}
                return {"ok": True, "resumed": False, "workflow": self._save(new)}
            if action == "activity" and (not current or current["state"] != "active" or current["workflow_id"] != workflow_id):
                return {"ok": True, "recorded": False}
            # Exact-ID repeated terminal requests are safe after a lost response.
            if (current and current["workflow_id"] == workflow_id and current["state"] == "completed"
                and ((action == "cancel" and current.get("completion_reason") == "cancelled_by_master")
                     or (action == "complete" and current.get("completion_reason") != "cancelled_by_master"))):
                return {"ok": True, "workflow": current}
            current = self.require_active(workflow_id)
            if dive_session_id and current["dive_session_id"] != dive_session_id:
                raise WorkflowError("Workflow belongs to a different Dive")
            if action in {"activity", "heartbeat"}:
                return {"ok": True, "recorded": True, "workflow": self._save(current)}
            if action == "resolve":
                members = self._members(current)
                if task_id not in members or resolution not in {"abandoned", "superseded"} or not note.strip() or len(note) > 2000:
                    raise WorkflowError("Resolution requires an owned Task, abandoned/superseded, and a short reason")
                task = self.inspect_task(task_id)
                state = task.get("state") or task.get("phase") or "unknown"
                if task.get("finalizing") or state not in {"failed", "interrupted", "cancelled", "unknown"}:
                    raise WorkflowError("Stop running work before resolving it")
                if resolution == "superseded":
                    if replacement_task_id not in members or replacement_task_id == task_id:
                        raise WorkflowError("Replacement must be another Task in this Workflow")
                    replacement = self.inspect_task(replacement_task_id)
                    if replacement.get("state") != "completed" or replacement.get("finalizing"):
                        raise WorkflowError("Replacement Task must have completed successfully")
                entry = {"kind": resolution, "note": note.strip(), "agent_session_id": task.get("agent_session_id"),
                         "replacement_task_id": replacement_task_id, "resolved_at": revision()}
                # An interrupted attempt may still reserve a workspace after a
                # restart. Retire it through AgentRuntime before closing work.
                if state == "interrupted":
                    if self.stop_task is None:
                        raise WorkflowError("Task retirement is unavailable")
                    await self.stop_task(task_id)
                    if self.inspect_task(task_id).get("state") != "cancelled":
                        raise WorkflowError("Task retirement has not finished")
                return {"ok": True, "workflow": self._save({**current,
                    "resolutions": {**current.get("resolutions", {}), task_id: entry}})}
            if action not in {"complete", "cancel"}:
                raise WorkflowError("Unsupported Workflow action")
            if action == "cancel":
                for member in self._members(current):
                    task = self.inspect_task(member)
                    if task.get("state") not in {"completed", "failed", "cancelled", "unknown"} or task.get("finalizing"):
                        if self.stop_task is None:
                            raise WorkflowError("Task cancellation is unavailable")
                        await self.stop_task(member)
                blocked = [task for task in self.blockers(current) if task["state"] not in {"failed", "unknown"}]
                if blocked:
                    return {"ok": False, "error": "Task cancellation is still finishing; retry workflow-cancel", "blockers": blocked, "workflow": current}
            if action == "complete":
                blocked = self.blockers(current)
                if not blocked:
                    build = await self.build_status()
                    if build["fingerprint"] != current.get("world_build_fingerprint_at_start") and not build.get("current"):
                        raise WorkflowError("workflow-complete blocked: finish changes and run the final World build")
                    blocked = self.blockers(current)
                if blocked:
                    return {"ok": False, "error": "Workflow has unfinished Tasks", "blockers": blocked, "workflow": current}
            finished = {**current, "state": "completed", "completed_at": revision(),
                        **({"completion_reason": "cancelled_by_master"} if action == "cancel" else {})}
            return {"ok": True, "workflow": self._save(finished)}
