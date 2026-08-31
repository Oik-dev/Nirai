from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import subprocess
import sys

from .config import ConfigError, load_config
from .logging_config import configure_core_logging, shutdown_core_logging
from .server import CoreServer


LOGGER = logging.getLogger("nirai.core.main")
WORLD_RESTART_DELAY_SEC = 30
WORLD_MAX_CONSECUTIVE_FAILURES = 5


def _windows_subprocess_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _new_holo_local_secret() -> str:
    return secrets.token_urlsafe(48)


def _holo_local_bridge_file() -> str | None:
    configured = os.environ.get("NIRAI_HOLO_LOCAL_BRIDGE_FILE", "").strip()
    if configured:
        return configured
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if not local_app_data:
        return None
    return os.path.join(local_app_data, "Nirai", "holo-local-bridge.json")


def _write_holo_local_bridge_file(*, core_port: int, secret: str, server_pid: int) -> None:
    path = _holo_local_bridge_file()
    if path is None:
        raise RuntimeError("LOCALAPPDATA is unavailable for Holo local bridge")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "version": 1,
        "url": f"ws://127.0.0.1:{core_port}",
        "secret": secret,
        "server_pid": server_pid,
    }
    temporary = f"{path}.{server_pid}.tmp"
    with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def _clear_holo_local_bridge_file(expected_pid: int) -> None:
    path = _holo_local_bridge_file()
    if path is None:
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            descriptor = json.load(handle)
        if descriptor.get("server_pid") != expected_pid:
            return
        os.remove(path)
    except FileNotFoundError:
        return
    except (OSError, json.JSONDecodeError, AttributeError):
        LOGGER.warning("holo_local_bridge_file_clear_failed", exc_info=True)


async def _launch_world(nirai_root: str) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["NIRAI_ROOT"] = nirai_root
    world_root = os.path.join(nirai_root, "world")
    dev_mode = env.get("NIRAI_WORLD_DEV") == "1"
    if dev_mode:
        command = ("npm.cmd", "run", "dev")
        mode = "dev"
    else:
        electron_path = os.path.join(
            world_root,
            "node_modules",
            "electron",
            "dist",
            "electron.exe",
        )
        main_bundle = os.path.join(world_root, "out", "main", "index.js")
        if not os.path.isfile(electron_path):
            raise RuntimeError(f"Electron runtime not found: {electron_path}")
        if not os.path.isfile(main_bundle):
            raise RuntimeError("Nirai World build is missing. Run npm run build in world first.")
        command = (electron_path, ".")
        mode = "production"

    LOGGER.info("world_launch_start mode=%s", mode)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=world_root,
        env=env,
        stdout=asyncio.subprocess.DEVNULL if not dev_mode else None,
        stderr=asyncio.subprocess.DEVNULL if not dev_mode else None,
        creationflags=_windows_subprocess_flags(),
    )
    LOGGER.info("world_launch_success pid=%s mode=%s", process.pid, mode)
    return process


async def _stop_world(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return

    LOGGER.info("world_stop_start pid=%s", process.pid)
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


async def _run() -> None:
    config = load_config()
    configure_core_logging(config.root, config.core.log_level)
    LOGGER.info("core_start root=%s port=%s", config.root, config.core.port)
    holo_local_secret = _new_holo_local_secret()
    server = CoreServer(config, holo_local_secret=holo_local_secret)
    world_process: asyncio.subprocess.Process | None = None
    server_task: asyncio.Task[None] | None = None
    process_pid = os.getpid()

    await server.start()
    try:
        _write_holo_local_bridge_file(
            core_port=server.bound_port or config.core.port,
            secret=holo_local_secret,
            server_pid=process_pid,
        )
        LOGGER.info("holo_local_bridge_ready")
    except Exception:
        LOGGER.exception("holo_local_bridge_unavailable core_continues=true")
    if sys.stdout is not None:
        print(f"Nirai Core listening on ws://{server.host}:{config.core.port}", flush=True)
    try:
        server_task = asyncio.create_task(server.run_forever(), name="nirai-core-server")
        consecutive_failures = 0

        while True:
            try:
                world_process = await _launch_world(str(config.root))
            except Exception:
                consecutive_failures += 1
                LOGGER.exception(
                    "world_launch_failed attempt=%s/%s",
                    consecutive_failures,
                    WORLD_MAX_CONSECUTIVE_FAILURES,
                )
            else:
                world_wait_task = asyncio.create_task(
                    world_process.wait(),
                    name="nirai-world-process",
                )
                done, _ = await asyncio.wait(
                    {server_task, world_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if server_task in done:
                    world_wait_task.cancel()
                    await asyncio.gather(world_wait_task, return_exceptions=True)
                    await server_task
                    break

                returncode = world_wait_task.result()
                world_process = None
                if returncode == 0:
                    LOGGER.info("world_exit_normal returncode=%s", returncode)
                    break

                consecutive_failures += 1
                LOGGER.warning(
                    "world_exit_unexpected returncode=%s attempt=%s/%s",
                    returncode,
                    consecutive_failures,
                    WORLD_MAX_CONSECUTIVE_FAILURES,
                )

            if consecutive_failures >= WORLD_MAX_CONSECUTIVE_FAILURES:
                LOGGER.error(
                    "world_restart_abandoned failures=%s core_continues=true",
                    consecutive_failures,
                )
                await server_task
                break

            LOGGER.info(
                "world_restart_scheduled delay_sec=%s attempt=%s/%s",
                WORLD_RESTART_DELAY_SEC,
                consecutive_failures + 1,
                WORLD_MAX_CONSECUTIVE_FAILURES,
            )
            try:
                await asyncio.wait_for(
                    asyncio.shield(server_task),
                    timeout=WORLD_RESTART_DELAY_SEC,
                )
            except TimeoutError:
                continue
            await server_task
            break
    finally:
        if server_task is not None and not server_task.done():
            server_task.cancel()
        if server_task is not None:
            await asyncio.gather(server_task, return_exceptions=True)
        await _stop_world(world_process)
        _clear_holo_local_bridge_file(process_pid)
        await server.stop()
        LOGGER.info("core_stop")


def main() -> int:
    try:
        asyncio.run(_run())
    except ConfigError as exc:
        if sys.stderr is not None:
            print(f"Nirai Core configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        LOGGER.info("core_keyboard_interrupt")
        return 0
    except Exception:
        LOGGER.exception("core_fatal_error")
        return 1
    finally:
        shutdown_core_logging()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
