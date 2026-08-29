from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import tomllib


class ConfigError(RuntimeError):
    """Raised when Nirai's root configuration is missing or invalid."""


@dataclass(frozen=True)
class CoreSettings:
    port: int
    log_level: str


@dataclass(frozen=True)
class WorldSettings:
    fps: int
    audio_volume: int
    voicevox_url: str


@dataclass(frozen=True)
class EcoModeSettings:
    resume_delay_sec: int


@dataclass(frozen=True)
class NiraiConfig:
    root: Path
    core: CoreSettings
    world: WorldSettings
    ecomode: EcoModeSettings
    residents_enabled: tuple[str, ...]
    tasks_allowed_dirs: tuple[str, ...]


def get_nirai_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _expect_table(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"config.toml: [{key}] is required")
    return value


def _expect_int(table: dict, key: str, section: str) -> int:
    value = table.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ConfigError(f"config.toml: {section}.{key} must be an integer")
    return value


def _expect_str(table: dict, key: str, section: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config.toml: {section}.{key} must be a non-empty string")
    return value


def _expect_str_list(table: dict, key: str, section: str) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ConfigError(f"config.toml: {section}.{key} must be an array of strings")
    return tuple(value)


def save_audio_volume(root: Path, volume: int) -> None:
    if not isinstance(volume, int) or isinstance(volume, bool) or not 0 <= volume <= 100:
        raise ConfigError("audio volume must be between 0 and 100")

    config_path = root.resolve() / "config.toml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"config.toml could not be updated: {exc}") from exc

    section_start: int | None = None
    section_end = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "[world]":
            section_start = index
            continue
        if section_start is not None and index > section_start and re.fullmatch(r"\[[^]]+\]", stripped):
            section_end = index
            break
    if section_start is None:
        raise ConfigError("config.toml: [world] section is required")

    replacement = f"audio_volume = {volume}"
    for index in range(section_start + 1, section_end):
        if re.match(r"^\s*audio_volume\s*=", lines[index]):
            lines[index] = replacement
            break
    else:
        lines.insert(section_start + 1, replacement)

    temporary = config_path.with_name(f".{config_path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(lines) + "\n")
        os.replace(temporary, config_path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def load_config(root: Path | None = None) -> NiraiConfig:
    nirai_root = (root or get_nirai_root()).resolve()
    config_path = nirai_root / "config.toml"

    if not config_path.is_file():
        raise ConfigError(f"config.toml not found: {config_path}")

    try:
        with config_path.open("rb") as file:
            data = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config.toml is invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"config.toml could not be read: {exc}") from exc

    core = _expect_table(data, "core")
    world = _expect_table(data, "world")
    ecomode = _expect_table(data, "ecomode")
    residents = _expect_table(data, "residents")
    tasks = _expect_table(data, "tasks")

    port = _expect_int(core, "port", "core")
    if not 1 <= port <= 65535:
        raise ConfigError("config.toml: core.port must be between 1 and 65535")

    log_level = _expect_str(core, "log_level", "core").upper()
    if log_level not in {"DEBUG", "INFO", "WARN", "ERROR"}:
        raise ConfigError("config.toml: core.log_level must be DEBUG, INFO, WARN, or ERROR")

    fps = _expect_int(world, "fps", "world")
    if fps <= 0:
        raise ConfigError("config.toml: world.fps must be greater than 0")

    audio_volume = _expect_int(world, "audio_volume", "world")
    if not 0 <= audio_volume <= 100:
        raise ConfigError("config.toml: world.audio_volume must be between 0 and 100")

    resume_delay_sec = _expect_int(ecomode, "resume_delay_sec", "ecomode")
    if resume_delay_sec < 0:
        raise ConfigError("config.toml: ecomode.resume_delay_sec must be 0 or greater")

    return NiraiConfig(
        root=nirai_root,
        core=CoreSettings(port=port, log_level=log_level),
        world=WorldSettings(
            fps=fps,
            audio_volume=audio_volume,
            voicevox_url=_expect_str(world, "voicevox_url", "world"),
        ),
        ecomode=EcoModeSettings(resume_delay_sec=resume_delay_sec),
        residents_enabled=_expect_str_list(residents, "enabled", "residents"),
        tasks_allowed_dirs=_expect_str_list(tasks, "allowed_dirs", "tasks"),
    )
