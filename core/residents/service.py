from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import tomllib
from typing import Any


LOGGER = logging.getLogger("nirai.core.residents")

# Special brain kind: the resident's mind is the Holo Addon (ChatGPT Web
# conversation), never a normal Brain Driver. See Docs/詳細設計/12.
HOLO_ADDON_BRAIN = "holo-addon"

_INVALID_WINDOWS_NAME_CHARS = set('<>:"/\\|?*')
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class ResidentError(RuntimeError):
    pass


@dataclass(frozen=True)
class ResidentTtsSettings:
    enabled: bool
    provider: str
    speaker_uuid: str | None
    style_id: int | None
    speed: float
    pitch: float
    intonation: float

    @property
    def configured(self) -> bool:
        return self.speaker_uuid is not None and self.style_id is not None

    def to_protocol(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "speaker_uuid": self.speaker_uuid,
            "style_id": self.style_id,
            "speed": self.speed,
            "pitch": self.pitch,
            "intonation": self.intonation,
        }


@dataclass(frozen=True)
class ResidentDefinition:
    name: str
    brain: str | None
    brain_model: str | None
    brain_reasoning_effort: str | None
    avatar: str | None
    tick_interval_min: int
    tick_budget: int | None
    spawn_location: str
    tts: ResidentTtsSettings

    def to_protocol(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "brain": self.brain,
            "brain_model": self.brain_model,
            "brain_reasoning_effort": self.brain_reasoning_effort,
            "avatar": self.avatar,
            "location": self.spawn_location,
            "tts": self.tts.to_protocol(),
        }


class ResidentService:
    def __init__(self, root: Path, enabled_names: tuple[str, ...] = ()) -> None:
        self.root = root.resolve()
        self.residents_root = self.root / "residents"
        self.config_path = self.root / "config.toml"
        self.residents_root.mkdir(parents=True, exist_ok=True)
        self._enabled_names = list(enabled_names)

    @property
    def enabled_names(self) -> tuple[str, ...]:
        return tuple(self._enabled_names)

    def list_enabled(self) -> list[ResidentDefinition]:
        residents: list[ResidentDefinition] = []
        for name in self._enabled_names:
            try:
                residents.append(self.load(name))
            except ResidentError as exc:
                LOGGER.warning("resident_load_skipped name=%s error=%s", name, exc)
        return residents

    def load(self, name: str) -> ResidentDefinition:
        resident_dir = self._resident_dir(name)
        config_path = resident_dir / "config.toml"
        if not config_path.is_file():
            raise ResidentError(f"Resident config not found: {name}")

        try:
            with config_path.open("rb") as handle:
                raw = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ResidentError(f"Resident config could not be read: {name}: {exc}") from exc

        brain = _optional_non_empty_str(raw.get("brain"))
        brain_model = _optional_non_empty_str(raw.get("brain_model"))
        brain_reasoning_effort = _optional_non_empty_str(raw.get("brain_reasoning_effort"))
        avatar = _optional_non_empty_str(raw.get("avatar"))
        tick_interval_min = _positive_int(raw.get("tick_interval_min"), 30)
        tick_budget = _optional_non_negative_int(raw.get("tick_budget"))
        spawn_location = _optional_non_empty_str(raw.get("spawn_location")) or "center"
        tts_raw = raw.get("tts") if isinstance(raw.get("tts"), dict) else {}
        tts = ResidentTtsSettings(
            enabled=_bool_value(tts_raw.get("enabled"), True),
            provider=_optional_non_empty_str(tts_raw.get("provider")) or "voicevox",
            speaker_uuid=_optional_non_empty_str(tts_raw.get("speaker_uuid")),
            style_id=_optional_int(tts_raw.get("style_id")),
            speed=_number_value(tts_raw.get("speed"), 1.0),
            pitch=_number_value(tts_raw.get("pitch"), 0.0),
            intonation=_number_value(tts_raw.get("intonation"), 1.0),
        )
        return ResidentDefinition(
            name=name,
            brain=brain,
            brain_model=brain_model,
            brain_reasoning_effort=brain_reasoning_effort,
            avatar=avatar,
            tick_interval_min=tick_interval_min,
            tick_budget=tick_budget,
            spawn_location=spawn_location,
            tts=tts,
        )

    def create(
        self,
        requested_name: str,
        brain: str,
        brain_model: str | None = None,
        brain_reasoning_effort: str | None = None,
    ) -> ResidentDefinition:
        name = self.validate_new_name(requested_name)
        provider = self.validate_brain_provider(brain)
        self._assert_holo_addon_slot_free(provider)
        # Holo Addon has no selectable model: the brain is the ChatGPT Web
        # conversation itself, so a model value would be meaningless.
        model = None if provider == HOLO_ADDON_BRAIN else self.validate_brain_model(brain_model)
        reasoning_effort = self.validate_brain_reasoning_effort(provider, brain_reasoning_effort)
        resident_dir = self._resident_dir(name)
        if resident_dir.exists():
            raise ResidentError(f"同名のResident「{name}」が既に存在します")

        avatar = self._initial_avatar_for(name)
        resident_dir.mkdir(parents=False)
        try:
            _atomic_write_text(resident_dir / "persona.md", _persona_template(name))
            _atomic_write_text(
                resident_dir / "config.toml",
                _default_config_text(
                    brain=provider,
                    brain_model=model,
                    brain_reasoning_effort=reasoning_effort,
                    avatar=avatar,
                ),
            )
            self._enabled_names.append(name)
            self._write_enabled_names()
        except Exception:
            if name in self._enabled_names:
                self._enabled_names.remove(name)
            shutil.rmtree(resident_dir, ignore_errors=True)
            raise

        LOGGER.info(
            "resident_created name=%s brain=%s model=%s reasoning=%s avatar=%s",
            name,
            provider,
            model,
            reasoning_effort,
            avatar,
        )
        return self.load(name)

    def validate_brain_provider(self, provider: str) -> str:
        cleaned = provider.strip()
        if cleaned not in {"codex", "claude-code", "cursor", "gemini", HOLO_ADDON_BRAIN}:
            raise ResidentError("現在選択できるAIはCodex / Claude / Cursor / Gemini / Holo Addonです")
        return cleaned

    def _assert_holo_addon_slot_free(self, provider: str, *, exclude: str | None = None) -> None:
        if provider != HOLO_ADDON_BRAIN:
            return
        for definition in self.list_enabled():
            if definition.name != exclude and definition.brain == HOLO_ADDON_BRAIN:
                raise ResidentError(
                    f"Holo Addonを頭脳にできるResidentは1人だけです（現在: {definition.name}）"
                )

    def validate_brain_model(self, model: str | None) -> str | None:
        if model is None:
            return None
        cleaned = model.strip()
        if not cleaned:
            return None
        if len(cleaned) > 200 or any(ord(character) < 32 for character in cleaned):
            raise ResidentError("AI Model名が不正です")
        return cleaned

    def validate_brain_reasoning_effort(
        self,
        provider: str,
        reasoning_effort: str | None,
    ) -> str | None:
        if reasoning_effort is None:
            return None
        cleaned = reasoning_effort.strip().casefold()
        if not cleaned:
            return None
        if provider != "codex":
            raise ResidentError("推論強度はCodexでのみ指定できます")
        if cleaned not in {"low", "medium", "high", "xhigh", "ultra", "max"}:
            raise ResidentError("Codex推論強度が不正です")
        return cleaned

    def reorder(self, names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        requested = tuple(name.strip() for name in names if isinstance(name, str))
        if len(requested) != len(self._enabled_names):
            raise ResidentError("Resident並び順が現在のResident一覧と一致しません")
        if len(set(requested)) != len(requested):
            raise ResidentError("Resident並び順に重複があります")
        if set(requested) != set(self._enabled_names):
            raise ResidentError("Resident並び順が現在のResident一覧と一致しません")
        self._enabled_names = list(requested)
        self._write_enabled_names()
        LOGGER.info("resident_reordered names=%s", ",".join(requested))
        return tuple(self._enabled_names)

    def set_brain(
        self,
        name: str,
        provider: str,
        brain_model: str | None = None,
        brain_reasoning_effort: str | None = None,
    ) -> ResidentDefinition:
        self.load(name)
        cleaned = self.validate_brain_provider(provider)
        self._assert_holo_addon_slot_free(cleaned, exclude=name)
        model = None if cleaned == HOLO_ADDON_BRAIN else self.validate_brain_model(brain_model)
        reasoning_effort = self.validate_brain_reasoning_effort(cleaned, brain_reasoning_effort)
        self._set_top_level_string(name, "brain", cleaned)
        self._set_top_level_optional_string(name, "brain_model", model)
        self._set_top_level_optional_string(name, "brain_reasoning_effort", reasoning_effort)
        LOGGER.info(
            "resident_brain_updated name=%s provider=%s model=%s reasoning=%s",
            name,
            cleaned,
            model,
            reasoning_effort,
        )
        return self.load(name)

    def _initial_avatar_for(self, name: str) -> str | None:
        if name.casefold() != "lapan":
            return None
        candidate = self.root / "avatars" / "lapan" / "lapan.vrm"
        return "lapan/lapan.vrm" if candidate.is_file() else None

    def validate_new_name(self, requested_name: str) -> str:
        name = requested_name.strip()
        if not name:
            raise ResidentError("Resident名を入力してください")
        if name in {".", ".."}:
            raise ResidentError("そのResident名は使用できません")
        if name.endswith(".") or name.endswith(" "):
            raise ResidentError("Resident名の末尾にピリオドまたは空白は使用できません")
        if any(ord(character) < 32 for character in name):
            raise ResidentError("Resident名に制御文字は使用できません")
        invalid = sorted({character for character in name if character in _INVALID_WINDOWS_NAME_CHARS})
        if invalid:
            raise ResidentError(f"Resident名に使用できない文字が含まれています: {''.join(invalid)}")
        stem = name.split(".", 1)[0].upper()
        if stem in _RESERVED_WINDOWS_NAMES:
            raise ResidentError(f"Windowsで予約されている名前は使用できません: {name}")

        existing = {candidate.name.casefold() for candidate in self.residents_root.iterdir() if candidate.is_dir()}
        if name.casefold() in existing or any(enabled.casefold() == name.casefold() for enabled in self._enabled_names):
            raise ResidentError(f"同名のResident「{name}」が既に存在します")
        return name

    def delete(self, name: str, confirm: str) -> None:
        if confirm != "Delete":
            raise ResidentError('Resident削除には"Delete"の完全一致が必要です')
        self.load(name)
        resident_dir = self._resident_dir(name)
        old_enabled_names = list(self._enabled_names)
        self._enabled_names = [enabled for enabled in self._enabled_names if enabled.casefold() != name.casefold()]
        try:
            self._write_enabled_names()
            shutil.rmtree(resident_dir)
        except Exception as exc:
            self._enabled_names = old_enabled_names
            try:
                self._write_enabled_names()
            except Exception:
                LOGGER.exception("resident_delete_rollback_failed name=%s", name)
            if isinstance(exc, ResidentError):
                raise
            raise ResidentError(f"Resident could not be deleted: {name}: {exc}") from exc
        LOGGER.info("resident_deleted name=%s", name)

    def set_tts(self, name: str, value: object) -> ResidentDefinition:
        self.load(name)
        if not isinstance(value, dict):
            raise ResidentError("Resident TTS settings must be an object")

        enabled = value.get("enabled")
        provider = value.get("provider")
        speaker_uuid = value.get("speaker_uuid")
        style_id = value.get("style_id")
        speed = value.get("speed")
        pitch = value.get("pitch")
        intonation = value.get("intonation")
        if not isinstance(enabled, bool):
            raise ResidentError("TTS enabled must be boolean")
        if provider != "voicevox":
            raise ResidentError("M1 supports only the voicevox TTS provider")
        if not isinstance(speaker_uuid, str) or not speaker_uuid.strip():
            raise ResidentError("VOICEVOX speaker_uuid is required")
        if not isinstance(style_id, int) or isinstance(style_id, bool):
            raise ResidentError("VOICEVOX style_id must be an integer")
        numbers = {"speed": speed, "pitch": pitch, "intonation": intonation}
        if any(
            not isinstance(number, (int, float))
            or isinstance(number, bool)
            or not math.isfinite(float(number))
            for number in numbers.values()
        ):
            raise ResidentError("VOICEVOX tuning values must be finite numbers")

        self._set_section_values(
            name,
            "tts",
            {
                "enabled": enabled,
                "provider": provider,
                "speaker_uuid": speaker_uuid.strip(),
                "style_id": style_id,
                "speed": float(speed),
                "pitch": float(pitch),
                "intonation": float(intonation),
            },
        )
        LOGGER.info("resident_tts_updated name=%s provider=voicevox style_id=%s", name, style_id)
        return self.load(name)

    def set_avatar(self, name: str, avatar_path: str) -> ResidentDefinition:
        self.load(name)
        normalized = avatar_path.strip().replace("\\", "/")
        if not normalized:
            raise ResidentError("Avatar path is required")
        if Path(normalized).is_absolute():
            raise ResidentError("Avatar path must be relative to avatars root")
        if Path(normalized).suffix.lower() != ".vrm":
            raise ResidentError("Avatar path must use the .vrm extension")

        avatars_root = (self.root / "avatars").resolve()
        candidate = (avatars_root / normalized).resolve()
        try:
            candidate.relative_to(avatars_root)
        except ValueError as exc:
            raise ResidentError("Avatar path escaped avatars root") from exc
        if not candidate.is_file():
            raise ResidentError(f"Avatar file not found: {normalized}")

        self._set_top_level_string(name, "avatar", normalized)
        LOGGER.info("resident_avatar_updated name=%s avatar=%s", name, normalized)
        return self.load(name)

    def read_persona(self, name: str) -> str:
        persona_path = self._resident_dir(name) / "persona.md"
        try:
            return persona_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ResidentError(f"Resident persona could not be read: {name}: {exc}") from exc

    def _set_top_level_string(self, name: str, key: str, value: str) -> None:
        self._set_top_level_optional_string(name, key, value)

    def _set_top_level_optional_string(
        self,
        name: str,
        key: str,
        value: str | None,
    ) -> None:
        config_path = self._resident_dir(name) / "config.toml"
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ResidentError(f"Resident config could not be updated: {name}: {exc}") from exc

        replacement = None if value is None else f"{key} = {json.dumps(value, ensure_ascii=False)}"
        first_section = next(
            (index for index, line in enumerate(lines) if re.fullmatch(r"\s*\[[^]]+\]\s*", line)),
            len(lines),
        )
        for index in range(first_section):
            if re.match(rf"^\s*{re.escape(key)}\s*=", lines[index]):
                if replacement is None:
                    lines.pop(index)
                else:
                    lines[index] = replacement
                break
        else:
            if replacement is not None:
                insert_at = first_section
                if insert_at > 0 and lines[insert_at - 1].strip() == "":
                    insert_at -= 1
                lines.insert(insert_at, replacement)

        _atomic_write_text(config_path, "\n".join(lines) + "\n")

    def _set_section_values(self, name: str, section: str, values: dict[str, object]) -> None:
        config_path = self._resident_dir(name) / "config.toml"
        try:
            lines = config_path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise ResidentError(f"Resident config could not be updated: {name}: {exc}") from exc

        section_header = f"[{section}]"
        try:
            section_start = next(index for index, line in enumerate(lines) if line.strip() == section_header)
        except StopIteration:
            if lines and lines[-1].strip():
                lines.append("")
            section_start = len(lines)
            lines.append(section_header)

        section_end = len(lines)
        for index in range(section_start + 1, len(lines)):
            if re.fullmatch(r"\s*\[[^]]+\]\s*", lines[index]):
                section_end = index
                break

        for key, value in values.items():
            if isinstance(value, bool):
                encoded = "true" if value else "false"
            elif isinstance(value, str):
                encoded = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, float):
                encoded = repr(value)
            else:
                encoded = str(value)
            replacement = f"{key} = {encoded}"
            for index in range(section_start + 1, section_end):
                if re.match(rf"^\s*{re.escape(key)}\s*=", lines[index]):
                    lines[index] = replacement
                    break
            else:
                lines.insert(section_end, replacement)
                section_end += 1

        _atomic_write_text(config_path, "\n".join(lines) + "\n")

    def _resident_dir(self, name: str) -> Path:
        candidate = (self.residents_root / name).resolve()
        try:
            candidate.relative_to(self.residents_root.resolve())
        except ValueError as exc:
            raise ResidentError("Resident path escaped residents root") from exc
        return candidate

    def _write_enabled_names(self) -> None:
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ResidentError(f"config.toml could not be updated: {exc}") from exc

        lines = text.splitlines()
        section_start: int | None = None
        section_end = len(lines)
        for index, line in enumerate(lines):
            stripped = line.strip()
            if stripped == "[residents]":
                section_start = index
                continue
            if section_start is not None and index > section_start and re.fullmatch(r"\[[^]]+\]", stripped):
                section_end = index
                break
        if section_start is None:
            raise ResidentError("config.toml: [residents] section is missing")

        enabled_line = "enabled = " + json.dumps(self._enabled_names, ensure_ascii=False)
        replaced = False
        for index in range(section_start + 1, section_end):
            if re.match(r"^\s*enabled\s*=", lines[index]):
                lines[index] = enabled_line
                replaced = True
                break
        if not replaced:
            lines.insert(section_start + 1, enabled_line)

        _atomic_write_text(self.config_path, "\n".join(lines) + "\n")


def _persona_template(name: str) -> str:
    return (
        f"# {name}\n\n"
        "## 性格\n\n"
        "## 口調\n\n"
        "## 日課\n\n"
        "## 得意\n\n"
        "## 決めごと\n"
    )


def _default_config_text(
    *,
    brain: str,
    brain_model: str | None,
    brain_reasoning_effort: str | None,
    avatar: str | None,
) -> str:
    lines = [
        f"brain = {json.dumps(brain, ensure_ascii=False)}",
    ]
    if brain_model is not None:
        lines.append(f"brain_model = {json.dumps(brain_model, ensure_ascii=False)}")
    if brain_reasoning_effort is not None:
        lines.append(
            f"brain_reasoning_effort = {json.dumps(brain_reasoning_effort, ensure_ascii=False)}"
        )
    if avatar is not None:
        lines.append(f"avatar = {json.dumps(avatar, ensure_ascii=False)}")
    lines.extend([
        "tick_interval_min = 30",
        'spawn_location = "center"',
        "",
        "[tts]",
        "enabled = true",
        'provider = "voicevox"',
        "speed = 1.0",
        "pitch = 0.0",
        "intonation = 1.0",
        "",
    ])
    return "\n".join(lines)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink(missing_ok=True)


def _optional_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return default


def _optional_non_negative_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _bool_value(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _number_value(value: object, default: float) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default
