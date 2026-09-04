from __future__ import annotations

import asyncio
from dataclasses import dataclass
import locale
import logging
import os
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Mapping, Sequence

from .base import BrainError


LOGGER = logging.getLogger("nirai.core.brain.process")
PROCESS_STOP_STEP_TIMEOUT_SEC = 2.0


def _windows_subprocess_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class InvocationTimeoutError(BrainError):
    pass


@dataclass(frozen=True)
class CompletedInvocation:
    returncode: int
    stdout: str
    stderr: str


def decode_process_output(data: bytes) -> str:
    encodings = ("utf-8", locale.getpreferredencoding(False))
    for encoding in dict.fromkeys(encodings):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


class ProcessManager:
    def __init__(self) -> None:
        self._active: dict[str, asyncio.subprocess.Process] = {}

    async def run(
        self,
        invocation_id: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_sec: float,
        stdin_text: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CompletedInvocation:
        if invocation_id in self._active:
            raise BrainError(f"duplicate invocation_id: {invocation_id}")

        started_at = perf_counter()
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdin=asyncio.subprocess.PIPE if stdin_text is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(env) if env is not None else None,
                creationflags=_windows_subprocess_flags(),
            )
        except OSError as exc:
            LOGGER.error(
                "process_start_failed invocation_id=%s error_type=%s error=%s",
                invocation_id,
                type(exc).__name__,
                exc,
            )
            raise BrainError(f"Brain process could not start: {exc}") from exc
        self._active[invocation_id] = process
        LOGGER.info("process_started invocation_id=%s pid=%s", invocation_id, process.pid)
        try:
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(
                        stdin_text.encode("utf-8") if stdin_text is not None else None
                    ),
                    timeout=timeout_sec,
                )
            except TimeoutError as exc:
                LOGGER.warning(
                    "process_timeout invocation_id=%s pid=%s elapsed_ms=%d timeout_sec=%s",
                    invocation_id,
                    process.pid,
                    round((perf_counter() - started_at) * 1000),
                    timeout_sec,
                )
                await self.cancel(invocation_id)
                raise InvocationTimeoutError(
                    f"Brain invocation timed out after {timeout_sec:g}s"
                ) from exc
            LOGGER.info(
                "process_finished invocation_id=%s pid=%s returncode=%s elapsed_ms=%d",
                invocation_id,
                process.pid,
                process.returncode,
                round((perf_counter() - started_at) * 1000),
            )
            return CompletedInvocation(
                returncode=process.returncode or 0,
                stdout=decode_process_output(stdout),
                stderr=decode_process_output(stderr),
            )
        except asyncio.CancelledError:
            # Cancellation of the owner must not orphan the CLI process. This
            # path is also used when Core shutdown gives an invocation cancel a
            # finite outer deadline.
            await self._stop_process(invocation_id, process)
            raise
        finally:
            self._active.pop(invocation_id, None)

    async def cancel(self, invocation_id: str) -> bool:
        process = self._active.get(invocation_id)
        if process is None or process.returncode is not None:
            LOGGER.info("process_cancel_noop invocation_id=%s", invocation_id)
            return False
        return await self._stop_process(invocation_id, process)

    async def _stop_process(
        self,
        invocation_id: str,
        process: asyncio.subprocess.Process,
    ) -> bool:
        if process.returncode is not None:
            return True

        LOGGER.info("process_cancel_start invocation_id=%s pid=%s", invocation_id, process.pid)
        taskkill_returncode: int | None = None
        try:
            killer = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "taskkill.exe",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=_windows_subprocess_flags(),
                ),
                timeout=PROCESS_STOP_STEP_TIMEOUT_SEC,
            )
            try:
                await asyncio.wait_for(
                    killer.wait(),
                    timeout=PROCESS_STOP_STEP_TIMEOUT_SEC,
                )
                taskkill_returncode = killer.returncode
            except asyncio.TimeoutError:
                LOGGER.warning(
                    "process_taskkill_timeout invocation_id=%s pid=%s",
                    invocation_id,
                    process.pid,
                )
                try:
                    killer.kill()
                except (ProcessLookupError, OSError):
                    pass
        except (asyncio.TimeoutError, OSError):
            LOGGER.warning(
                "process_taskkill_failed invocation_id=%s pid=%s",
                invocation_id,
                process.pid,
                exc_info=True,
            )

        if await self._wait_process_exit(process):
            stopped = True
        else:
            stopped = await self._terminate_process_fallback(invocation_id, process)

        LOGGER.info(
            "process_cancel_done invocation_id=%s pid=%s taskkill_returncode=%s process_returncode=%s stopped=%s",
            invocation_id,
            process.pid,
            taskkill_returncode,
            process.returncode,
            stopped,
        )
        return stopped

    async def _wait_process_exit(self, process: asyncio.subprocess.Process) -> bool:
        if process.returncode is not None:
            return True
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=PROCESS_STOP_STEP_TIMEOUT_SEC,
            )
        except asyncio.TimeoutError:
            return False
        return process.returncode is not None

    async def _terminate_process_fallback(
        self,
        invocation_id: str,
        process: asyncio.subprocess.Process,
    ) -> bool:
        for action_name, action in (("terminate", process.terminate), ("kill", process.kill)):
            if process.returncode is not None:
                return True
            try:
                action()
            except (ProcessLookupError, OSError):
                LOGGER.warning(
                    "process_%s_failed invocation_id=%s pid=%s",
                    action_name,
                    invocation_id,
                    process.pid,
                    exc_info=True,
                )
            if await self._wait_process_exit(process):
                return True
        LOGGER.error(
            "process_stop_failed invocation_id=%s pid=%s",
            invocation_id,
            process.pid,
        )
        return process.returncode is not None
