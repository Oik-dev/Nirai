from __future__ import annotations

import asyncio
from datetime import datetime
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.world_runtime import WorldRuntimeProcess


LOGGER = logging.getLogger("nirai.world.launcher")


@dataclass(frozen=True)
class StandardWorldLauncher:
    """Launcher for the distributable Electron + Three.js standard World."""

    dev_mode: bool | None = None
    core_url: str = "ws://127.0.0.1:8765"
    voicevox_url: str = "http://127.0.0.1:50021"
    max_fps: int = 60
    resume_delay_sec: int = 0

    async def launch(self, nirai_root: Path, world_secret: str) -> WorldRuntimeProcess:
        root = nirai_root.resolve()
        env = os.environ.copy()
        env["NIRAI_ROOT"] = str(root)
        env["NIRAI_WORLD_SECRET"] = world_secret
        env["NIRAI_CORE_URL"] = self.core_url
        env["NIRAI_VOICEVOX_URL"] = self.voicevox_url
        env["NIRAI_WORLD_MAX_FPS"] = str(self.max_fps)
        env["NIRAI_ECOMODE_RESUME_DELAY_SEC"] = str(self.resume_delay_sec)
        world_root = root / "world"
        dev_mode = self.dev_mode if self.dev_mode is not None else env.get("NIRAI_WORLD_DEV") == "1"
        if dev_mode:
            command = ("npm.cmd", "run", "dev")
            mode = "dev"
        else:
            electron_path = world_root / "node_modules" / "electron" / "dist" / "electron.exe"
            main_bundle = world_root / "out" / "main" / "index.js"
            if not electron_path.is_file():
                raise RuntimeError(f"Electron runtime not found: {electron_path}")
            if not main_bundle.is_file():
                raise RuntimeError("Nirai World build is missing. Run npm run build in world first.")
            command = (str(electron_path), ".")
            mode = "production"

        LOGGER.info("world_launch_start runtime=standard-electron mode=%s", mode)
        world_log = None
        if not dev_mode:
            log_path = root / "runtime" / "logs" / f"world-process-{datetime.now().astimezone():%Y%m%d}.log"
            try:
                log_path.parent.mkdir(parents=True, exist_ok=True)
                world_log = log_path.open("ab", buffering=0)
            except OSError:
                LOGGER.warning("world_process_log_unavailable path=%s", log_path, exc_info=True)
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(world_root),
                env=env,
                stdout=world_log if world_log is not None else (asyncio.subprocess.DEVNULL if not dev_mode else None),
                stderr=world_log if world_log is not None else (asyncio.subprocess.DEVNULL if not dev_mode else None),
                creationflags=_windows_subprocess_flags(),
            )
        finally:
            if world_log is not None:
                world_log.close()
        LOGGER.info("world_launch_success pid=%s runtime=standard-electron mode=%s", process.pid, mode)
        return process

    async def stop(self, process: WorldRuntimeProcess | None) -> None:
        if process is None or process.returncode is not None:
            return

        LOGGER.info("world_stop_start pid=%s runtime=standard-electron", process.pid)
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_windows_subprocess_flags(),
                )
                await asyncio.wait_for(killer.wait(), timeout=5)
            except (OSError, TimeoutError):
                LOGGER.warning("world_tree_stop_failed pid=%s", process.pid, exc_info=True)

        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            LOGGER.warning("world_stop_timeout pid=%s", process.pid)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        LOGGER.info("world_stop_done pid=%s returncode=%s", process.pid, process.returncode)


def _windows_subprocess_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
