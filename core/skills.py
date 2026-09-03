from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path


LOGGER = logging.getLogger("nirai.core.skills")
SKILL_FILENAME = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024
MAX_TOTAL_SKILL_BYTES = 128 * 1024


@dataclass(frozen=True)
class SkillDocument:
    name: str
    description: str
    body: str

    def to_public_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "description": self.description,
            "content": self.body,
        }


class SkillRegistry:
    """Provider-neutral Nirai Skill loader.

    The registry intentionally reads only `skills/<name>/SKILL.md`. Provider
    native/global Skill directories are not consulted, so Nirai behavior does
    not depend on whichever CLI happens to back a Resident.

    Files are read on demand rather than cached. This keeps the plumbing simple
    and lets a newly added Skill take effect on the next Brain call or Holo
    `skills` request without restarting Core.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def load(self) -> tuple[SkillDocument, ...]:
        if not self.root.is_dir():
            return ()

        loaded: list[SkillDocument] = []
        total_bytes = 0
        try:
            directories = sorted(
                (entry for entry in self.root.iterdir() if entry.is_dir()),
                key=lambda entry: entry.name.casefold(),
            )
        except OSError:
            LOGGER.warning("skill_registry_list_failed path=%s", self.root, exc_info=True)
            return ()

        for directory in directories:
            path = directory / SKILL_FILENAME
            if not path.is_file():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                LOGGER.warning("skill_stat_failed path=%s", path, exc_info=True)
                continue
            if size > MAX_SKILL_BYTES:
                LOGGER.warning("skill_skipped_too_large path=%s bytes=%s", path, size)
                continue
            if total_bytes + size > MAX_TOTAL_SKILL_BYTES:
                LOGGER.warning(
                    "skill_skipped_total_limit path=%s total_bytes=%s next_bytes=%s",
                    path,
                    total_bytes,
                    size,
                )
                continue

            try:
                raw = path.read_text(encoding="utf-8")
                document = self._parse(directory.name, raw)
            except (OSError, UnicodeError, ValueError) as exc:
                LOGGER.warning(
                    "skill_skipped_invalid path=%s error=%s",
                    path,
                    str(exc)[:300],
                )
                continue

            loaded.append(document)
            total_bytes += size

        return tuple(loaded)

    def prompt_context(self) -> str:
        skills = self.load()
        if not skills:
            return ""

        sections: list[str] = []
        for skill in skills:
            sections.append(
                f"<nirai-skill name={skill.name!r}>\n"
                f"description: {skill.description}\n\n"
                f"{skill.body}\n"
                "</nirai-skill>"
            )
        return "\n\n".join(sections)

    def public_payload(self) -> dict[str, object]:
        skills = self.load()
        return {
            "count": len(skills),
            "skills": [skill.to_public_dict() for skill in skills],
        }

    @staticmethod
    def _parse(directory_name: str, raw: str) -> SkillDocument:
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        if not lines or lines[0].strip() != "---":
            raise ValueError("SKILL.md must begin with YAML front matter")

        closing_index: int | None = None
        for index in range(1, min(len(lines), 128)):
            if lines[index].strip() == "---":
                closing_index = index
                break
        if closing_index is None:
            raise ValueError("SKILL.md front matter is not closed")

        metadata: dict[str, str] = {}
        for line in lines[1:closing_index]:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or ":" not in stripped:
                continue
            key, value = stripped.split(":", 1)
            cleaned_key = key.strip().casefold()
            cleaned_value = value.strip().strip('"').strip("'")
            if cleaned_key in {"name", "description"} and cleaned_value:
                metadata[cleaned_key] = cleaned_value

        name = metadata.get("name", "")
        description = metadata.get("description", "")
        if not name:
            raise ValueError("SKILL.md front matter requires name")
        if name != directory_name:
            raise ValueError("SKILL.md name must match its directory name")
        if not description:
            raise ValueError("SKILL.md front matter requires description")

        body = "\n".join(lines[closing_index + 1 :]).strip()
        if not body:
            raise ValueError("SKILL.md body must not be empty")

        return SkillDocument(name=name, description=description, body=body)
