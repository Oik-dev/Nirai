import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.__main__ as core_main
import world.launcher as world_launcher


class FakeWorldProcess:
    def __init__(self, returncode: int) -> None:
        self._planned_returncode = returncode
        self.returncode: int | None = None
        self.pid = 12345

    async def wait(self) -> int:
        await asyncio.sleep(0)
        self.returncode = self._planned_returncode
        return self._planned_returncode


class FakeWorldLauncher:
    def __init__(self, processes: list[FakeWorldProcess]) -> None:
        self.processes = list(processes)
        self.launch_count = 0
        self.stopped: list[FakeWorldProcess | None] = []

    async def launch(self, _root, _secret: str) -> FakeWorldProcess:
        process = self.processes[self.launch_count]
        self.launch_count += 1
        return process

    async def stop(self, process) -> None:
        self.stopped.append(process)


class FakeCoreServer:
    host = "127.0.0.1"
    bound_port = 8765

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
        world=SimpleNamespace(voicevox_url="http://127.0.0.1:51234", fps=48, audio_volume=100),
        ecomode=SimpleNamespace(resume_delay_sec=7),
    )


def _stub_holo_bridge(monkeypatch) -> None:
    monkeypatch.setattr(core_main, "_new_holo_local_secret", lambda: "generated-secret")
    monkeypatch.setattr(core_main, "_write_holo_local_bridge_file", lambda **_kwargs: None)
    monkeypatch.setattr(core_main, "_clear_holo_local_bridge_file", lambda _pid: None)


def test_run_stops_core_when_world_exits_cleanly(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    launcher = FakeWorldLauncher([FakeWorldProcess(0)])
    _stub_holo_bridge(monkeypatch)

    asyncio.run(core_main._run(launcher))

    assert server.started is True
    assert server.stopped is True


def test_run_builds_default_world_launcher_from_bound_core_and_config(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    server.bound_port = 9876
    launcher = FakeWorldLauncher([FakeWorldProcess(0)])
    captured: dict[str, object] = {}

    def make_launcher(**kwargs):
        captured.update(kwargs)
        return launcher

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    monkeypatch.setattr(core_main, "StandardWorldLauncher", make_launcher)
    _stub_holo_bridge(monkeypatch)

    asyncio.run(core_main._run())

    assert captured == {
        "core_url": "ws://127.0.0.1:9876",
        "voicevox_url": "http://127.0.0.1:51234",
        "max_fps": 48,
        "resume_delay_sec": 7,
    }
    assert launcher.launch_count == 1


def test_run_restarts_world_after_unexpected_exit(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    launcher = FakeWorldLauncher([FakeWorldProcess(1), FakeWorldProcess(0)])

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    monkeypatch.setattr(core_main, "WORLD_RESTART_DELAY_SEC", 0)
    _stub_holo_bridge(monkeypatch)

    asyncio.run(core_main._run(launcher))

    assert launcher.launch_count == 2
    assert server.stopped is True


def test_run_exits_core_after_consecutive_world_failures(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    launcher = FakeWorldLauncher([FakeWorldProcess(1) for _ in range(5)])

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    monkeypatch.setattr(core_main, "WORLD_RESTART_DELAY_SEC", 0)
    _stub_holo_bridge(monkeypatch)

    with pytest.raises(RuntimeError, match="World failed 5 consecutive times"):
        asyncio.run(core_main._run(launcher))

    assert launcher.launch_count == 5
    assert server.stopped is True


def test_world_failure_streak_resets_after_a_stable_runtime(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    launcher = FakeWorldLauncher([
        FakeWorldProcess(1),
        FakeWorldProcess(1),
        FakeWorldProcess(1),
        FakeWorldProcess(1),
        FakeWorldProcess(1),
        FakeWorldProcess(1),
        FakeWorldProcess(0),
    ])

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    monkeypatch.setattr(core_main, "WORLD_RESTART_DELAY_SEC", 0)
    monkeypatch.setattr(core_main, "WORLD_STABLE_RUNTIME_SEC", 0)
    _stub_holo_bridge(monkeypatch)

    async def scenario() -> None:
        await asyncio.wait_for(core_main._run(launcher), timeout=1)

    asyncio.run(scenario())

    assert launcher.launch_count == 7
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

    monkeypatch.setattr(world_launcher.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(world_launcher.StandardWorldLauncher().launch(tmp_path, "world-secret"))

    args, kwargs = calls[0]
    assert args == (str(electron), ".")
    assert kwargs["cwd"] == str(world)
    assert kwargs["stdout"] is kwargs["stderr"]
    assert kwargs["stdout"].closed is True
    assert Path(kwargs["stdout"].name).parent == tmp_path / "runtime" / "logs"
    assert Path(kwargs["stdout"].name).name.startswith("world-process-")
    assert kwargs["env"]["NIRAI_WORLD_SECRET"] == "world-secret"
    assert kwargs["env"]["NIRAI_CORE_URL"] == "ws://127.0.0.1:8765"
    assert kwargs["env"]["NIRAI_VOICEVOX_URL"] == "http://127.0.0.1:50021"
    assert kwargs["env"]["NIRAI_WORLD_MAX_FPS"] == "60"
    assert kwargs["env"]["NIRAI_ECOMODE_RESUME_DELAY_SEC"] == "0"


def test_launch_world_keeps_npm_dev_only_for_explicit_development_start(tmp_path, monkeypatch) -> None:
    world = tmp_path / "world"
    world.mkdir()
    monkeypatch.setenv("NIRAI_WORLD_DEV", "1")
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeWorldProcess(0)

    monkeypatch.setattr(world_launcher.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(world_launcher.StandardWorldLauncher().launch(tmp_path, "world-secret"))

    args, kwargs = calls[0]
    assert args == ("npm.cmd", "run", "dev")
    assert kwargs["cwd"] == str(world)
    assert kwargs["stdout"] is None
    assert kwargs["stderr"] is None
    assert kwargs["env"]["NIRAI_WORLD_SECRET"] == "world-secret"


def test_holo_local_bridge_descriptor_lives_outside_project_and_contains_only_connection_data(tmp_path, monkeypatch) -> None:
    bridge_file = tmp_path / "LocalAppData" / "Nirai" / "holo-local-bridge.json"
    monkeypatch.setenv("NIRAI_HOLO_LOCAL_BRIDGE_FILE", str(bridge_file))

    core_main._write_holo_local_bridge_file(
        core_port=9876,
        secret="local-secret",
        server_pid=4567,
    )

    payload = json.loads(bridge_file.read_text(encoding="utf-8"))
    assert payload == {
        "version": 1,
        "url": "ws://127.0.0.1:9876",
        "secret": "local-secret",
        "server_pid": 4567,
    }

    core_main._clear_holo_local_bridge_file(9999)
    assert bridge_file.exists()
    core_main._clear_holo_local_bridge_file(4567)
    assert not bridge_file.exists()


def test_run_uses_same_local_secret_for_core_and_bridge_descriptor(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    captured = {}

    def create_server(_config, *, holo_local_secret=None, world_secret=None):
        captured["core_secret"] = holo_local_secret
        captured["core_world_secret"] = world_secret
        return server

    def write_bridge(*, core_port: int, secret: str, server_pid: int):
        captured["bridge_secret"] = secret
        captured["core_port"] = core_port
        captured["server_pid"] = server_pid

    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "_new_holo_local_secret", lambda: "generated-secret")
    monkeypatch.setattr(core_main, "_new_world_secret", lambda: "generated-world-secret")
    monkeypatch.setattr(core_main, "CoreServer", create_server)
    monkeypatch.setattr(core_main, "_write_holo_local_bridge_file", write_bridge)
    monkeypatch.setattr(core_main, "_clear_holo_local_bridge_file", lambda _pid: None)

    class CapturingLauncher(FakeWorldLauncher):
        async def launch(self, root, secret: str) -> FakeWorldProcess:
            captured["world_process_secret"] = secret
            return await super().launch(root, secret)

    launcher = CapturingLauncher([FakeWorldProcess(0)])

    asyncio.run(core_main._run(launcher))

    assert captured["core_secret"] == "generated-secret"
    assert captured["bridge_secret"] == "generated-secret"
    assert captured["core_world_secret"] == "generated-world-secret"
    assert captured["world_process_secret"] == "generated-world-secret"
    assert captured["core_port"] == 8765
    assert isinstance(captured["server_pid"], int)


def test_world_stop_failure_does_not_skip_bridge_and_core_cleanup(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    cleared = []
    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    _stub_holo_bridge(monkeypatch)
    monkeypatch.setattr(core_main, "_clear_holo_local_bridge_file", cleared.append)

    class FailingStopLauncher(FakeWorldLauncher):
        async def stop(self, process) -> None:
            raise RuntimeError("World stop failed")

    with pytest.raises(RuntimeError, match="World stop failed"):
        asyncio.run(core_main._run(FailingStopLauncher([FakeWorldProcess(0)])))

    assert server.stopped
    assert len(cleared) == 1


def test_cancelled_run_awaits_world_waiter_cleanup_before_returning(tmp_path, monkeypatch) -> None:
    server = FakeCoreServer()
    monkeypatch.setattr(core_main, "load_config", lambda: fake_config(tmp_path))
    monkeypatch.setattr(core_main, "configure_core_logging", lambda *_args: None)
    monkeypatch.setattr(core_main, "CoreServer", lambda _config, **_kwargs: server)
    _stub_holo_bridge(monkeypatch)

    async def scenario() -> None:
        waiting = asyncio.Event()
        finished = asyncio.Event()

        class WaitingWorldProcess(FakeWorldProcess):
            async def wait(self) -> int:
                waiting.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    finished.set()
                return 0

        launcher = FakeWorldLauncher([WaitingWorldProcess(0)])
        run = asyncio.create_task(core_main._run(launcher))
        await asyncio.wait_for(waiting.wait(), timeout=2)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert finished.is_set()
        assert server.stopped
        assert len(launcher.stopped) == 1

    asyncio.run(scenario())
