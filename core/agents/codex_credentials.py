"""Codex credential homes and environment isolation.

Lifecycle and cancellation remain owned by CodexAppServerAdapter. Methods use
that Adapter's ownership registry and preserve its cleanup override points.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import time

from .base import AgentRuntimeUnavailableError
from .safety import AgentWorkspacePolicy

_HOME_REMOVE_RETRY_DELAYS_SEC = (0.0, 0.05, 0.1, 0.2, 0.4)
_CODEX_STALE_RUNTIME_AGE_SEC = 6 * 60 * 60


class CodexCredentialsMixin:
    workspace_policy: AgentWorkspacePolicy

    def _prepare_isolated_codex_home(
        self,
        agent_session_id: str,
        *,
        conversation_id: str | None = None,
    ) -> Path:
        self._cleanup_stale_agent_homes()
        source_home = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).resolve()
        source_auth = source_home / "auth.json"
        if not source_auth.is_file():
            raise AgentRuntimeUnavailableError("Codex authentication is unavailable")

        if conversation_id is None:
            isolated_root = (self.workspace_policy.root / "runtime" / "codex_agent_homes").resolve()
            isolated_home = (isolated_root / agent_session_id).resolve()
            self._remove_isolated_home(isolated_home)
            isolated_home.mkdir(parents=True, exist_ok=False)
        else:
            isolated_root = (
                self.workspace_policy.root / "runtime" / "codex_conversation_homes"
            ).resolve()
            digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:24]
            isolated_home = (isolated_root / f"CV-{digest}").resolve()
            try:
                isolated_home.relative_to(isolated_root)
            except ValueError as exc:
                raise AgentRuntimeUnavailableError("Codex Conversation home path is invalid") from exc
            isolated_home.mkdir(parents=True, exist_ok=True)
            self._remove_conversation_secret_material(isolated_home)

        try:
            isolated_home.relative_to(isolated_root)
        except ValueError as exc:
            raise AgentRuntimeUnavailableError("Codex Agent home path is invalid") from exc

        auth_path = isolated_home / "auth.json"
        try:
            shutil.copyfile(source_auth, auth_path)
            self._restrict_auth_permissions(auth_path)
        except (OSError, AgentRuntimeUnavailableError) as exc:
            if conversation_id is None:
                self._remove_isolated_home(isolated_home)
            else:
                # A transient auth-copy failure must not erase durable native
                # thread state for the Conversation. Remove only credential
                # material and let a later turn retry with the same thread.
                self._remove_conversation_secret_material(isolated_home)
            raise AgentRuntimeUnavailableError("Codex authentication could not be isolated") from exc
        return isolated_home

    @classmethod
    def _remove_conversation_secret_material(cls, home: Path) -> None:
        """Remove transient credential-bearing files while keeping thread state.

        Conversation homes persist Codex's native thread/session database so a
        later Agent Session can use thread/resume without replaying Nirai's raw
        transcript. Authentication is copied in only for the active turn and is
        removed again after the app-server process is fully stopped.
        """
        for name in ("auth.json", "cap_sid", ".sandbox-secrets"):
            path = home / name
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            except OSError as exc:
                raise AgentRuntimeUnavailableError(
                    f"Codex Conversation secret cleanup failed: {name}"
                ) from exc

    def discard_conversation_context(self, conversation_id: str) -> None:
        isolated_root = (
            self.workspace_policy.root / "runtime" / "codex_conversation_homes"
        ).resolve()
        digest = hashlib.sha256(conversation_id.encode("utf-8")).hexdigest()[:24]
        home = (isolated_root / f"CV-{digest}").resolve()
        try:
            home.relative_to(isolated_root)
        except ValueError as exc:
            raise AgentRuntimeUnavailableError("Codex Conversation home path is invalid") from exc
        self._remove_isolated_home(home)

    def _cleanup_stale_agent_homes(self) -> None:
        isolated_root = (self.workspace_policy.root / "runtime" / "codex_agent_homes").resolve()
        if isolated_root.is_dir():
            owned_ids = self._runtime_owned_snapshot()
            for child in isolated_root.iterdir():
                if child.name in owned_ids or not self._runtime_path_is_stale(child):
                    continue
                self._remove_isolated_home(child)

        # A pre-M4 implementation placed a complete Codex Home inside the
        # Agent workspace. It must never survive into a new Agent run.
        legacy_home = (
            self.workspace_policy.root
            / "runtime"
            / "workspace"
            / "m4-codex-agent-home"
        ).resolve()
        self._remove_isolated_home(legacy_home)

    @staticmethod
    def _runtime_path_is_stale(path: Path) -> bool:
        # In-process ownership covers concurrent starts in one Core. Another
        # Core process cannot share that set, so young homes are conservatively
        # protected and become cleanup candidates only after the same 6-hour
        # stale window used by Cursor runtime state.
        try:
            return time.time() - path.stat().st_mtime >= _CODEX_STALE_RUNTIME_AGE_SEC
        except OSError:
            return False

    @staticmethod
    def _remove_isolated_home(path: Path) -> None:
        if not path.exists():
            return
        last_error: OSError | None = None
        for attempt, delay_sec in enumerate(_HOME_REMOVE_RETRY_DELAYS_SEC):
            if attempt > 0 and delay_sec > 0:
                time.sleep(delay_sec)
            try:
                shutil.rmtree(path)
            except OSError as exc:
                # Windows can keep Codex sqlite files briefly locked after the
                # app-server process tree exits. Retry for a short bounded
                # window, but never hide a persistent cleanup failure.
                last_error = exc
                continue
            if not path.exists():
                return
        if last_error is not None:
            raise AgentRuntimeUnavailableError(
                f"Codex Agent credential home could not be removed: {path.name}"
            ) from last_error
        raise AgentRuntimeUnavailableError(
            f"Codex Agent credential home still exists after cleanup: {path.name}"
        )

    @staticmethod
    def _restrict_auth_permissions(auth_path: Path) -> None:
        if os.name != "nt":
            return
        username = os.environ.get("USERNAME", "").strip()
        if not username:
            raise AgentRuntimeUnavailableError("Windows user is unavailable for Codex auth ACL")
        domain = os.environ.get("USERDOMAIN", "").strip()
        principal = f"{domain}\\{username}" if domain else username
        result = subprocess.run(
            [
                "icacls.exe",
                str(auth_path),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(F)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode != 0:
            raise AgentRuntimeUnavailableError("Codex Agent auth ACL could not be restricted")

    @staticmethod
    def _build_child_env(isolated_codex_home: Path) -> dict[str, str]:
        allowed_names = {
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE",
            "PROCESSOR_IDENTIFIER",
            "OS",
        }
        child_env = {
            name: value
            for name, value in os.environ.items()
            if name.upper() in allowed_names
        }
        isolated = str(isolated_codex_home)
        child_env["CODEX_HOME"] = isolated
        child_env["USERPROFILE"] = isolated
        child_env["HOME"] = isolated
        child_env["PYTHONIOENCODING"] = "utf-8"
        return child_env
