from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .logging_config import configure_core_logging, shutdown_core_logging
from .server import CoreServer
from .world_runtime import WorldRuntimeLauncher, WorldRuntimeProcess
from world.launcher import StandardWorldLauncher


LOGGER = logging.getLogger("nirai.core.main")
WORLD_RESTART_DELAY_SEC = 30
WORLD_MAX_CONSECUTIVE_FAILURES = 5
WORLD_STABLE_RUNTIME_SEC = 60


def _new_holo_local_secret() -> str:
    return secrets.token_urlsafe(48)


def _new_world_secret() -> str:
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


async def _run(world_launcher: WorldRuntimeLauncher | None = None) -> None:
    config = load_config()
    configure_core_logging(config.root, config.core.log_level)
    LOGGER.info("core_start root=%s port=%s", config.root, config.core.port)
    holo_local_secret = _new_holo_local_secret()
    world_secret = _new_world_secret()
    server = CoreServer(
        config,
        holo_local_secret=holo_local_secret,
        world_secret=world_secret,
    )
    launcher = world_launcher
    world_process: WorldRuntimeProcess | None = None
    server_task: asyncio.Task[None] | None = None
    world_wait_task: asyncio.Task[int] | None = None
    process_pid = os.getpid()

    await server.start()
    if launcher is None:
        launcher = StandardWorldLauncher(
            core_url=f"ws://127.0.0.1:{server.bound_port or config.core.port}",
            voicevox_url=config.world.voicevox_url,
            max_fps=config.world.fps,
            resume_delay_sec=config.ecomode.resume_delay_sec,
        )
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
                world_process = await launcher.launch(Path(config.root), world_secret)
            except Exception:
                consecutive_failures += 1
                LOGGER.exception(
                    "world_launch_failed attempt=%s/%s",
                    consecutive_failures,
                    WORLD_MAX_CONSECUTIVE_FAILURES,
                )
            else:
                world_started_at = asyncio.get_running_loop().time()
                world_wait_task = asyncio.create_task(
                    world_process.wait(),
                    name="nirai-world-process",
                )
                done, _ = await asyncio.wait(
                    {server_task, world_wait_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if server_task in done:
                    await server_task
                    break

                returncode = world_wait_task.result()
                world_process = None
                runtime_sec = asyncio.get_running_loop().time() - world_started_at
                if returncode == 0:
                    LOGGER.info("world_exit_normal returncode=%s runtime_sec=%.1f", returncode, runtime_sec)
                    break

                if runtime_sec >= WORLD_STABLE_RUNTIME_SEC and consecutive_failures:
                    LOGGER.info(
                        "world_failure_streak_reset runtime_sec=%.1f previous_failures=%s",
                        runtime_sec,
                        consecutive_failures,
                    )
                    consecutive_failures = 0
                consecutive_failures += 1
                LOGGER.warning(
                    "world_exit_unexpected returncode=%s runtime_sec=%.1f attempt=%s/%s",
                    returncode,
                    runtime_sec,
                    consecutive_failures,
                    WORLD_MAX_CONSECUTIVE_FAILURES,
                )

            if consecutive_failures >= WORLD_MAX_CONSECUTIVE_FAILURES:
                LOGGER.error(
                    "world_restart_abandoned failures=%s core_exits=true",
                    consecutive_failures,
                )
                raise RuntimeError(
                    f"World failed {consecutive_failures} consecutive times; Core is shutting down"
                )

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
        # Both waiters belong to this lifecycle, including external cancellation.
        waiters = [task for task in (world_wait_task, server_task) if task is not None]
        for task in waiters:
            if not task.done():
                task.cancel()
        await asyncio.gather(*waiters, return_exceptions=True)
        try:
            await launcher.stop(world_process)
        finally:
            try:
                _clear_holo_local_bridge_file(process_pid)
            finally:
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
