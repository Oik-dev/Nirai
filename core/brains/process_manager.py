from __future__ import annotations

import asyncio
from dataclasses import dataclass
import locale
import logging
from pathlib import Path
from time import perf_counter
from typing import Sequence

from .base import BrainError


LOGGER = logging.getLogger("nirai.core.brain.process")


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
        finally:
            self._active.pop(invocation_id, None)

    async def cancel(self, invocation_id: str) -> bool:
        process = self._active.get(invocation_id)
        if process is None or process.returncode is not None:
            LOGGER.info("process_cancel_noop invocation_id=%s", invocation_id)
            return False

        LOGGER.info("process_cancel_start invocation_id=%s pid=%s", invocation_id, process.pid)
        killer = await asyncio.create_subprocess_exec(
            "taskkill.exe",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
        await process.wait()
        LOGGER.info(
            "process_cancel_done invocation_id=%s pid=%s taskkill_returncode=%s process_returncode=%s",
            invocation_id,
            process.pid,
            killer.returncode,
            process.returncode,
        )
        return True
