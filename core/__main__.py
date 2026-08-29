from __future__ import annotations

import asyncio
import logging
import os
import sys

from .config import ConfigError, load_config
from .logging_config import configure_core_logging, shutdown_core_logging
from .server import CoreServer


LOGGER = logging.getLogger("nirai.core.main")
WORLD_RESTART_DELAY_SEC = 30
WORLD_MAX_CONSECUTIVE_FAILURES = 5


async def _launch_world(nirai_root: str) -> asyncio.subprocess.Process:
    env = os.environ.copy()
    env["NIRAI_ROOT"] = nirai_root
    LOGGER.info("world_launch_start")
    process = await asyncio.create_subprocess_exec(
        "npm.cmd",
        "run",
        "dev",
        cwd=os.path.join(nirai_root, "world"),
        env=env,
    )
    LOGGER.info("world_launch_success pid=%s", process.pid)
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
    server = CoreServer(config)
    world_process: asyncio.subprocess.Process | None = None
    server_task: asyncio.Task[None] | None = None

    await server.start()
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
        await server.stop()
        LOGGER.info("core_stop")


def main() -> int:
    try:
        asyncio.run(_run())
    except ConfigError as exc:
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
