import asyncio
from types import SimpleNamespace

import core.__main__ as core_main


class FakeWorldProcess:
    def __init__(self, returncode: int) -> None:
        self._planned_returncode = returncode
        self.returncode: int | None = None
        self.pid = 12345

    async def wait(self) -> int:
        await asyncio.sleep(0)
        self.returncode = self._planned_returncode
        return self._planned_returncode


class FakeCoreServer:
    host = "127.0.0.1"

    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def run_forever(self) -> None:
        await asyncio.Event().wait()

    async def stop(self) -> None:
        self.stopped = True


def fake_config(tmp_path):
    return SimpleNamespace(
        root=tmp_path,
        core=SimpleNamespace(port=8765, log_level="INFO"),
    )


def test_run_stops_core_when_world_exits_cleanly(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config: server)
    monkeypatch.setattr(
        core_main,
        "_launch_world",
        lambda _root: asyncio.sleep(0, result=FakeWorldProcess(0)),
    )

    asyncio.run(core_main._run())

    assert server.started is True
    assert server.stopped is True


def test_run_restarts_world_after_unexpected_exit(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    processes = [FakeWorldProcess(1), FakeWorldProcess(0)]
    launch_count = 0

    async def launch(_root: str) -> FakeWorldProcess:
        nonlocal launch_count
        process = processes[launch_count]
        launch_count += 1
        return process

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config: server)
    monkeypatch.setattr(core_main, "_launch_world", launch)
    monkeypatch.setattr(core_main, "WORLD_RESTART_DELAY_SEC", 0)

    asyncio.run(core_main._run())

    assert launch_count == 2
    assert server.stopped is True


def test_launch_world_uses_built_electron_directly_for_normal_start(tmp_path, monkeypatch) -> None:
    world = tmp_path / "world"
    electron = world / "node_modules" / "electron" / "dist" / "electron.exe"
    main_bundle = world / "out" / "main" / "index.js"
    electron.parent.mkdir(parents=True)
    main_bundle.parent.mkdir(parents=True)
    electron.write_bytes(b"exe")
    main_bundle.write_text("// built", encoding="utf-8")
    monkeypatch.delenv("NIRAI_WORLD_DEV", raising=False)
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeWorldProcess(0)

    monkeypatch.setattr(core_main.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(core_main._launch_world(str(tmp_path)))

    args, kwargs = calls[0]
    assert args == (str(electron), ".")
    assert kwargs["cwd"] == str(world)
    assert kwargs["stdout"] == asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] == asyncio.subprocess.DEVNULL


def test_launch_world_keeps_npm_dev_only_for_explicit_development_start(tmp_path, monkeypatch) -> None:
    world = tmp_path / "world"
    world.mkdir()
    monkeypatch.setenv("NIRAI_WORLD_DEV", "1")
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeWorldProcess(0)

    monkeypatch.setattr(core_main.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(core_main._launch_world(str(tmp_path)))

    args, kwargs = calls[0]
    assert args == ("npm.cmd", "run", "dev")
    assert kwargs["cwd"] == str(world)
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None
