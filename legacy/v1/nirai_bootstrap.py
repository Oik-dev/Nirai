from __future__ import annotations

import argparse
import ctypes
from dataclasses import dataclass
from datetime import datetime
import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys
import tomllib
from typing import Iterable

from core.provider_runtime import format_command, resolve_codex_command


SUPPORTED_PYTHON = (3, 12)
RUNTIME_MODULES = (
    ("websockets", "websockets"),
    ("sqlite_vec", "sqlite-vec"),
)
OPTIONAL_PROVIDER_COMMANDS = {
    "cursor": ("agent.exe", "agent.cmd", "agent", "cursor-agent.exe", "cursor-agent.cmd", "cursor-agent"),
    "codex": ("codex.exe", "codex.cmd", "codex"),
}
_CURSOR_VERSION_DIR = re.compile(
    r"^(?P<year>\d{4})\.(?P<month>\d{1,2})\.(?P<day>\d{1,2})"
    r"(?:-\d{2}-\d{2}-\d{2})?-[a-f0-9]+$"
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    fatal: bool = False


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _find_command(names: Iterable[str], *, local_app_data: str | None = None) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    if local_app_data:
        cursor_dir = Path(local_app_data) / "cursor-agent"
        for name in names:
            candidate = cursor_dir / name
            if candidate.is_file():
                return str(candidate)
    return None


def _cursor_version_key(candidate: Path) -> tuple[int, int, int]:
    match = _CURSOR_VERSION_DIR.fullmatch(candidate.name)
    if match is None:
        return (-1, -1, -1)
    return (
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
    )


def _resolve_cursor_runtime(local_app_data: str | None) -> str | None:
    launcher = _find_command(
        OPTIONAL_PROVIDER_COMMANDS["cursor"],
        local_app_data=local_app_data,
    )
    if launcher is None:
        return None
    path = Path(launcher)
    if path.suffix.casefold() != ".cmd":
        return str(path)

    direct_node = path.parent / "node.exe"
    direct_index = path.parent / "index.js"
    if direct_node.is_file() and direct_index.is_file():
        return f"{direct_node} {direct_index}"
    versions_dir = path.parent / "versions"
    if not versions_dir.is_dir():
        return None
    try:
        candidates = sorted(
            (
                candidate
                for candidate in versions_dir.iterdir()
                if candidate.is_dir() and _CURSOR_VERSION_DIR.fullmatch(candidate.name)
            ),
            key=_cursor_version_key,
            reverse=True,
        )
    except OSError:
        return None
    for candidate in candidates:
        node_path = candidate / "node.exe"
        index_path = candidate / "index.js"
        if node_path.is_file() and index_path.is_file():
            return f"{node_path} {index_path}"
    return None


def _resolve_codex_runtime() -> str | None:
    return format_command(resolve_codex_command())


def _gemini_api_key_file(root: Path) -> Path | None:
    path = root / "world" / ".env"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "GEMINI_API_KEY" and value.strip().strip('"').strip("'"):
            return path
    return None


def _config_contract_error(data: object) -> str | None:
    if not isinstance(data, dict):
        return "config.toml root must be a table"

    required_sections = ("core", "world", "ecomode", "residents", "tasks")
    for section in required_sections:
        if not isinstance(data.get(section), dict):
            return f"config.toml: [{section}] is required"

    core = data["core"]
    world = data["world"]
    ecomode = data["ecomode"]
    residents = data["residents"]
    tasks = data["tasks"]
    memory = data.get("memory", {})
    if not isinstance(memory, dict):
        return "config.toml: [memory] must be a table when present"

    port = core.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        return "config.toml: core.port must be an integer between 1 and 65535"
    log_level = core.get("log_level")
    if not isinstance(log_level, str) or log_level.strip().upper() not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        return "config.toml: core.log_level must be DEBUG, INFO, WARN, or ERROR"

    fps = world.get("fps")
    if not isinstance(fps, int) or isinstance(fps, bool) or fps <= 0:
        return "config.toml: world.fps must be a positive integer"
    audio_volume = world.get("audio_volume")
    if not isinstance(audio_volume, int) or isinstance(audio_volume, bool) or not 0 <= audio_volume <= 100:
        return "config.toml: world.audio_volume must be an integer between 0 and 100"
    voicevox_url = world.get("voicevox_url")
    if not isinstance(voicevox_url, str) or not voicevox_url.strip():
        return "config.toml: world.voicevox_url must be a non-empty string"

    resume_delay = ecomode.get("resume_delay_sec")
    if not isinstance(resume_delay, int) or isinstance(resume_delay, bool) or resume_delay < 0:
        return "config.toml: ecomode.resume_delay_sec must be an integer 0 or greater"

    for section_name, table, key in (
        ("residents", residents, "enabled"),
        ("tasks", tasks, "allowed_dirs"),
    ):
        values = table.get(key)
        if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
            return f"config.toml: {section_name}.{key} must be an array of strings"

    integer_rules = (
        ("embedding_dim", 1),
        ("background_interval_sec", 1),
        ("background_daily_limit", 0),
        ("query_embedding_daily_limit", 0),
        ("embedding_daily_budget", 0),
        ("private_background_interval_sec", 1),
    )
    defaults = {
        "embedding_dim": 768,
        "background_interval_sec": 60,
        "background_daily_limit": 400,
        "query_embedding_daily_limit": 500,
        "embedding_daily_budget": 900,
        "private_background_interval_sec": 60,
    }
    values: dict[str, int] = {}
    for key, minimum in integer_rules:
        value = memory.get(key, defaults[key])
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            qualifier = "positive" if minimum == 1 else "0 or greater"
            return f"config.toml: memory.{key} must be a {qualifier} integer"
        values[key] = value
    if values["background_daily_limit"] + values["query_embedding_daily_limit"] > values["embedding_daily_budget"]:
        return "config.toml: memory background/query embedding limits must fit within embedding_daily_budget"

    world_processor = str(memory.get("world_processor", "disabled")).strip().casefold()
    if world_processor not in {"disabled", "gemini"}:
        return "config.toml: memory.world_processor must be disabled or gemini"
    private_provider = str(memory.get("private_semantic_provider", "disabled")).strip().casefold()
    if private_provider not in {"disabled", "gemini"}:
        return "config.toml: memory.private_semantic_provider must be disabled or gemini"
    if world_processor == "gemini":
        if not str(memory.get("extraction_model", "gemini-3.5-flash-lite")).strip():
            return "config.toml: memory.extraction_model must be non-empty for Gemini"
        if not str(memory.get("embedding_model", "gemini-embedding-2")).strip():
            return "config.toml: memory.embedding_model must be non-empty for Gemini"
    if private_provider == "gemini" and not str(memory.get("embedding_model", "gemini-embedding-2")).strip():
        return "config.toml: memory.embedding_model must be non-empty for Private Gemini semantic recall"
    return None


def run_preflight(root: Path, *, world_dev: bool | None = None) -> list[CheckResult]:
    root = root.resolve()
    if world_dev is None:
        world_dev = os.environ.get("NIRAI_WORLD_DEV", "0").strip() == "1"

    results: list[CheckResult] = []
    venv_root = root / ".venv"
    executable = Path(sys.executable).resolve()
    results.append(CheckResult(
        "python-runtime",
        "OK" if _is_within(executable, venv_root) else "ERROR",
        f"{executable} (expected project runtime under {venv_root})",
        fatal=not _is_within(executable, venv_root),
    ))

    python_ok = sys.version_info[:2] == SUPPORTED_PYTHON
    results.append(CheckResult(
        "python-version",
        "OK" if python_ok else "ERROR",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} (supported: {SUPPORTED_PYTHON[0]}.{SUPPORTED_PYTHON[1]}.x)",
        fatal=not python_ok,
    ))

    for module_name, package_name in RUNTIME_MODULES:
        available = importlib.util.find_spec(module_name) is not None
        results.append(CheckResult(
            f"python-package:{package_name}",
            "OK" if available else "ERROR",
            "installed" if available else f"missing; run Setup Nirai Runtime.cmd",
            fatal=not available,
        ))

    config_path = root / "config.toml"
    config_ok = False
    config_detail = str(config_path)
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
        contract_error = _config_contract_error(config)
        if contract_error is not None:
            config_detail = contract_error
        else:
            config_ok = True
    except FileNotFoundError:
        config_detail = "config.toml is missing"
    except (OSError, tomllib.TOMLDecodeError) as exc:
        config_detail = f"config.toml cannot be read: {exc}"
    results.append(CheckResult("config", "OK" if config_ok else "ERROR", config_detail, fatal=not config_ok))

    electron = root / "world" / "node_modules" / "electron" / "dist" / "electron.exe"
    electron_ok = electron.is_file()
    results.append(CheckResult(
        "world-electron",
        "OK" if electron_ok else "ERROR",
        str(electron) if electron_ok else "Electron runtime is missing; run npm --prefix world ci",
        fatal=not electron_ok,
    ))

    if world_dev:
        dev_entry = root / "world" / "node_modules" / ".bin" / "electron-vite.cmd"
        dev_ok = dev_entry.is_file()
        results.append(CheckResult(
            "world-dev-tooling",
            "OK" if dev_ok else "ERROR",
            str(dev_entry) if dev_ok else "electron-vite is missing; run npm --prefix world ci",
            fatal=not dev_ok,
        ))
    else:
        main_bundle = root / "world" / "out" / "main" / "index.js"
        build_ok = main_bundle.is_file()
        results.append(CheckResult(
            "world-production-build",
            "OK" if build_ok else "ERROR",
            str(main_bundle) if build_ok else "Production build is missing; run npm run build",
            fatal=not build_ok,
        ))

    local_app_data = os.environ.get("LOCALAPPDATA")
    provider_runtimes = {
        "cursor": _resolve_cursor_runtime(local_app_data),
        "codex": _resolve_codex_runtime(),
    }
    for provider, runtime in provider_runtimes.items():
        results.append(CheckResult(
            f"optional-provider:{provider}",
            "OK" if runtime else "WARN",
            runtime or "launcher/runtime not fully resolvable; Nirai Core can still start and this provider remains unavailable until configured",
            fatal=False,
        ))

    # Claude is intentionally not selectable in the current Nirai build after
    # the last live acceptance returned subscription HTTP 403. A CLI binary by
    # itself must not make Doctor claim the Provider is usable.
    results.append(CheckResult(
        "optional-provider:claude",
        "WARN",
        "disabled by current Nirai acceptance; Core can still start",
        fatal=False,
    ))

    gemini_key_file = _gemini_api_key_file(root)
    results.append(CheckResult(
        "optional-provider:gemini",
        "OK" if gemini_key_file is not None else "WARN",
        str(gemini_key_file) if gemini_key_file is not None else "GEMINI_API_KEY was not found in world/.env; Core can still start",
        fatal=False,
    ))
    return results


def format_report(results: list[CheckResult]) -> str:
    lines = ["Nirai startup preflight"]
    for result in results:
        lines.append(f"[{result.status}] {result.name}: {result.detail}")
    fatal_count = sum(result.fatal and result.status == "ERROR" for result in results)
    lines.append(f"fatal={fatal_count} warnings={sum(result.status == 'WARN' for result in results)}")
    return "\n".join(lines)


def _write_report(root: Path, report: str) -> Path | None:
    path = root / "runtime" / "logs" / "startup-preflight.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().astimezone().isoformat(timespec="seconds")
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"\n=== {stamp} ===\n{report}\n")
    except OSError:
        return None
    return path


def _show_startup_error(message: str) -> None:
    if sys.stderr is not None:
        print(message, file=sys.stderr)
        return
    if os.name == "nt":
        try:
            ctypes.windll.user32.MessageBoxW(None, message, "Nirai startup failed", 0x10)
            return
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nirai startup preflight / bootstrap")
    parser.add_argument("--doctor", action="store_true", help="run checks only")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent
    results = run_preflight(root)
    report = format_report(results)
    report_path = _write_report(root, report)

    if args.doctor and sys.stdout is not None:
        print(report)

    fatal = [result for result in results if result.fatal and result.status == "ERROR"]
    if fatal:
        suffix = f"\n\nDetails: {report_path}" if report_path else ""
        _show_startup_error(report + suffix)
        return 2
    if args.doctor:
        return 0

    try:
        from core.__main__ import main as core_main
    except Exception as exc:
        message = f"Nirai Core import failed after preflight: {type(exc).__name__}: {exc}"
        _write_report(root, message)
        _show_startup_error(message)
        return 2
    exit_code = core_main()
    if exit_code != 0 and sys.stderr is None:
        _show_startup_error(
            f"Nirai Core exited with code {exit_code}.\n\n"
            "Run Nirai Doctor.cmd and check runtime\\logs for details."
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
