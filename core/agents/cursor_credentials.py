"""Cursor credential isolation and provider filesystem permissions."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path

from .base import AgentRuntimeError, AgentRuntimeUnavailableError
from .cursor_policy import CURSOR_HOME_CLEANUP_RETRIES, _ALLOWED_ENV_NAMES
from .cursor_protocol import _bounded_text

LOGGER = logging.getLogger("nirai.core.agent.cursor_acp")


class CursorCredentialsMixin:
    """Credential operations using the Adapter's runtime ownership registry."""

    root: Path

    def _prepare_cursor_home(
        self,
        agent_session_id: str,
        *,
        working_dir: Path | None = None,
        extra_denied_paths: tuple[Path, ...] = (),
        stable_key: str | None = None,
    ) -> Path:
        if stable_key is None:
            homes_root = self.root / "runtime" / "cursor_agent_homes"
            homes_root.mkdir(parents=True, exist_ok=True)
            self._cleanup_stale_cursor_homes(homes_root)
            target = homes_root / agent_session_id
            if target.exists():
                self._cleanup_cursor_home(target)
        else:
            homes_root = self.root / "runtime" / "cursor_conversation_homes"
            homes_root.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32]
            target = homes_root / f"CV-{digest}"
            target.mkdir(parents=True, exist_ok=True)
            # A previous crash may have left the injected login state behind.
            # Prefer losing provider-native continuity over retaining a copied
            # credential indefinitely.
            try:
                self._remove_cursor_auth_copy(target)
            except AgentRuntimeError:
                self._cleanup_cursor_home(target)
                target.mkdir(parents=True, exist_ok=True)
        try:
            config_dir = target / ".cursor"
            config_dir.mkdir(parents=True, exist_ok=True)
            source = _cursor_auth_state_source(self.root)
            if source is None:
                raise AgentRuntimeUnavailableError(
                    "Cursor login state is unavailable. Sign in to Cursor Agent before using Cursor Agent Runtime."
                )
            auth_path = config_dir / "agent-cli-state.json"
            try:
                shutil.copyfile(source, auth_path)
                self._restrict_auth_permissions(auth_path)
            except (OSError, AgentRuntimeError) as exc:
                raise AgentRuntimeUnavailableError("Cursor authentication could not be isolated") from exc
            config = {
                "version": 1,
                "editor": {"vimMode": False},
                "permissions": {
                    "allow": [],
                    "deny": self._cursor_permission_denies(
                        working_dir,
                        extra_denied_paths=extra_denied_paths,
                    ),
                },
                "approvalMode": "allowlist",
                "notifications": False,
                "hints": False,
                "rewind": False,
                "suggestNextPrompt": False,
                "display": {
                    "showThinkingBlocks": False,
                    "showStatusIndicators": False,
                    "showStatusLineRunningTime": False,
                },
            }
            (config_dir / "cli-config.json").write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return target
        except BaseException:
            # Until this method returns, the caller does not own the home and
            # cannot clean it after a late permission/configuration failure.
            self._cleanup_cursor_home_after_turn(target, persistent=stable_key is not None)
            raise

    @staticmethod
    def _harden_cursor_cli_home(cursor_home: Path) -> None:
        config_path = cursor_home / ".cursor" / "cli-config.json"
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentRuntimeUnavailableError("Cursor CLI safety configuration could not be read") from exc
        permissions = config.get("permissions")
        if not isinstance(permissions, dict):
            permissions = {}
            config["permissions"] = permissions
        deny = permissions.get("deny")
        if not isinstance(deny, list):
            deny = []
        for rule in (
            "Shell(*)",
            "WebSearch(*)",
            "Browser(*)",
            "Computer(*)",
        ):
            if rule not in deny:
                deny.append(rule)
        permissions["deny"] = deny
        permissions["allow"] = []
        config["approvalMode"] = "allowlist"
        try:
            config_path.write_text(
                json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AgentRuntimeUnavailableError("Cursor CLI safety configuration could not be written") from exc

    def _build_cursor_environment(self, cursor_home: Path) -> dict[str, str]:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _ALLOWED_ENV_NAMES and isinstance(value, str)
        }
        temp = cursor_home / "Temp"
        temp.mkdir(parents=True, exist_ok=True)
        environment.update({
            "USERPROFILE": str(cursor_home),
            "HOME": str(cursor_home),
            "CURSOR_CONFIG_DIR": str(cursor_home / ".cursor"),
            # Cursor's cursor_login ACP authentication relies on the Windows
            # account's existing AppData-backed login path. Keep those two OS
            # locations for provider-internal auth only; CLI Read/Write denies
            # below block Agent tools from using them as data sources.
            "TEMP": str(temp),
            "TMP": str(temp),
        })
        return environment

    def _cursor_permission_denies(
        self,
        working_dir: Path | None,
        *,
        extra_denied_paths: tuple[Path, ...] = (),
    ) -> list[str]:
        denied_paths: set[Path] = set()
        actual_home = Path.home().resolve()
        denied_paths.add(actual_home)
        denied_paths.update(path.resolve() for path in extra_denied_paths)

        # Deny Nirai roots that ordinary task workers never need. The task's
        # own workspace is intentionally excluded.
        candidate_roots = [
            self.root / ".git",
            self.root / ".tools",
            self.root / "core",
            self.root / "world",
            self.root / "Docs",
            self.root / "residents",
            self.root / "avatars",
            self.root / "skills",
            self.root / "runtime" / "agent_sessions",
            self.root / "runtime" / "chat_sessions",
            self.root / "runtime" / "cursor_agent_homes",
            self.root / "runtime" / "cursor_conversation_homes",
            self.root / "runtime" / "cursor_profile",
            self.root / "runtime" / "world_memory",
        ]
        resolved_working = working_dir.resolve() if working_dir is not None else None
        for candidate in candidate_roots:
            resolved = candidate.resolve()
            if resolved_working is not None and (
                _path_is_within(resolved_working, resolved)
                or _path_is_within(resolved, resolved_working)
            ):
                continue
            denied_paths.add(resolved)

        # Existing sibling Task workspaces are also outside the current Task.
        if resolved_working is not None:
            workspace_root = (self.root / "runtime" / "workspace").resolve()
            if workspace_root.is_dir() and _path_is_within(resolved_working, workspace_root):
                for child in workspace_root.iterdir():
                    resolved = child.resolve()
                    if resolved == resolved_working or _path_is_within(resolved_working, resolved):
                        continue
                    denied_paths.add(resolved)

        # Shell cannot be safely path-confined by parsing arbitrary command text.
        # Cursor always works on a staging copy, and real-workspace writes must
        # happen only through Nirai's frozen review/apply path.
        deny = [
            "Shell(*)",
            "WebFetch(*)",
            "WebSearch(*)",
            "Browser(*)",
            "Computer(*)",
            "Mcp(*:*)",
            "Write(task.md)",
        ]
        for path in sorted(denied_paths, key=lambda item: str(item).casefold()):
            pattern = _cursor_permission_path(path)
            deny.append(f"Read({pattern})")
            deny.append(f"Write({pattern})")
        return deny

    def _cleanup_stale_cursor_homes(self, homes_root: Path) -> None:
        owned_ids = self._runtime_owned_snapshot()
        for child in homes_root.iterdir():
            if child.name in owned_ids or not self._runtime_path_is_stale(child):
                continue
            self._cleanup_cursor_home(child)

    @staticmethod
    def _remove_cursor_auth_copy(cursor_home: Path) -> None:
        auth_path = cursor_home / ".cursor" / "agent-cli-state.json"
        try:
            auth_path.unlink(missing_ok=True)
        except OSError as exc:
            raise AgentRuntimeError(
                f"Cursor conversation credential cleanup failed: {cursor_home.name}"
            ) from exc

    def _cleanup_cursor_conversation_credentials(self, cursor_home: Path) -> None:
        self._remove_cursor_auth_copy(cursor_home)
        for transient in (cursor_home / "Temp", cursor_home / ".nirai-staged-review"):
            try:
                shutil.rmtree(transient)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise AgentRuntimeError(
                    f"Cursor conversation transient cleanup failed: {transient.name}"
                ) from exc

    def _cleanup_cursor_home_after_turn(self, cursor_home: Path, *, persistent: bool) -> None:
        if not persistent:
            self._cleanup_cursor_home(cursor_home)
            return
        try:
            self._cleanup_cursor_conversation_credentials(cursor_home)
        except AgentRuntimeError:
            # Credential isolation outranks provider-native continuity. If the
            # injected auth copy cannot be scrubbed, remove the whole provider
            # context rather than leave a credential-bearing conversation home.
            LOGGER.warning(
                "cursor_conversation_credential_scrub_failed home=%s removing_context=true",
                cursor_home.name,
                exc_info=True,
            )
            self._cleanup_cursor_home(cursor_home)

    @staticmethod
    def _restrict_auth_permissions(auth_path: Path) -> None:
        if os.name != "nt":
            return
        username = os.environ.get("USERNAME", "").strip()
        if not username:
            raise AgentRuntimeUnavailableError("Windows user is unavailable for Cursor auth ACL")
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
            raise AgentRuntimeUnavailableError("Cursor Agent auth ACL could not be restricted")

    def _cleanup_cursor_home(self, cursor_home: Path) -> None:
        last_error: OSError | None = None
        for _ in range(CURSOR_HOME_CLEANUP_RETRIES):
            try:
                shutil.rmtree(cursor_home, ignore_errors=False)
                if not cursor_home.exists():
                    return
            except FileNotFoundError:
                return
            except OSError as exc:
                last_error = exc
        if cursor_home.exists():
            raise AgentRuntimeError(
                f"Cursor Agent credential home cleanup failed: {cursor_home.name}: {_bounded_text(last_error, 300)}"
            )


def _cursor_auth_state_source(root: Path) -> Path | None:
    candidates = [
        root / "runtime" / "cursor_profile" / ".cursor" / "agent-cli-state.json",
        Path.home() / ".cursor" / "agent-cli-state.json",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _path_is_within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _cursor_permission_path(path: Path) -> str:
    # Cursor CLI permission globs use forward slashes on all platforms.
    return path.resolve().as_posix().rstrip("/") + "/**"
