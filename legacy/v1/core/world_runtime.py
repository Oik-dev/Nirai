from __future__ import annotations

from pathlib import Path
from typing import Protocol


class WorldRuntimeProcess(Protocol):
    pid: int
    returncode: int | None

    async def wait(self) -> int:
        ...

    def terminate(self) -> None:
        ...

    def kill(self) -> None:
        ...


class WorldRuntimeLauncher(Protocol):
    """Runtime-neutral boundary used by Core lifecycle orchestration."""

    async def launch(self, nirai_root: Path, world_secret: str) -> WorldRuntimeProcess:
        ...

    async def stop(self, process: WorldRuntimeProcess | None) -> None:
        ...
