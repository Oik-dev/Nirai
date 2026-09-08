from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import re


LOGGER = logging.getLogger("nirai.core.skills")
SKILL_FILENAME = "SKILL.md"
MAX_SKILL_BYTES = 64 * 1024
MAX_TOTAL_SKILL_BYTES = 128 * 1024
MAX_SKILL_INDEX_CHARS = 8 * 1024


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


@dataclass(frozen=True)
class SkillIndexEntry:
    name: str
    description: str


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

    def index(self) -> tuple[SkillIndexEntry, ...]:
        """Read only Skill front matter, independent from body size budgets."""
        if not self.root.is_dir():
            return ()
        entries: list[SkillIndexEntry] = []
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
                with path.open("r", encoding="utf-8") as handle:
                    head = handle.read(MAX_SKILL_INDEX_CHARS)
                name, description, _ = self._parse_front_matter(directory.name, head)
            except (OSError, UnicodeError, ValueError) as exc:
                LOGGER.warning(
                    "skill_index_skipped_invalid path=%s error=%s",
                    path,
                    str(exc)[:300],
                )
                continue
            entries.append(SkillIndexEntry(name=name, description=description))
        return tuple(entries)

    def load_skill(self, name: str) -> SkillDocument | None:
        cleaned = name.strip()
        if not cleaned or Path(cleaned).name != cleaned or cleaned in {".", ".."}:
            return None
        path = self.root / cleaned / SKILL_FILENAME
        if not path.is_file():
            return None
        try:
            size = path.stat().st_size
        except OSError:
            LOGGER.warning("skill_stat_failed path=%s", path, exc_info=True)
            return None
        if size > MAX_SKILL_BYTES:
            LOGGER.warning("skill_selected_too_large path=%s bytes=%s", path, size)
            return None
        try:
            return self._parse(cleaned, path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            LOGGER.warning("skill_selected_invalid path=%s error=%s", path, str(exc)[:300])
            return None

    def prompt_context(self) -> str:
        """Return the compact Skill index only, never Skill bodies."""
        entries = self.index()
        if not entries:
            return ""
        lines = ["<nirai-skill-index>"]
        lines.extend(f"- {entry.name}: {entry.description}" for entry in entries)
        lines.append("</nirai-skill-index>")
        return "\n".join(lines)

    def augment_task_prompt(self, task_text: str) -> str:
        """Attach the Skill index and only bodies relevant to this Task."""
        cleaned = task_text.strip()
        entries = self.index()
        if not entries:
            return cleaned
        selected = self._select_relevant(entries, cleaned)
        sections = [cleaned, "Nirai Skill Index:\n" + self.prompt_context()]
        if selected:
            selected_sections: list[str] = []
            total_bytes = 0
            for entry in selected:
                skill = self.load_skill(entry.name)
                if skill is None:
                    continue
                size = len(skill.body.encode("utf-8"))
                if total_bytes + size > MAX_TOTAL_SKILL_BYTES:
                    continue
                total_bytes += size
                selected_sections.append(
                    f"<nirai-skill name={skill.name!r}>\n"
                    f"description: {skill.description}\n\n"
                    f"{skill.body}\n"
                    "</nirai-skill>"
                )
            if selected_sections:
                sections.append(
                    "Nirai Skills selected from the index for this Task:\n"
                    + "\n\n".join(selected_sections)
                )
        return "\n\n".join(sections)

    @staticmethod
    def _select_relevant(
        entries: tuple[SkillIndexEntry, ...],
        task_text: str,
        *,
        max_skills: int = 3,
    ) -> tuple[SkillIndexEntry, ...]:
        query = task_text.casefold()
        query_words = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", query))
        query_jp = _japanese_trigrams(query)
        scored: list[tuple[int, str, SkillIndexEntry]] = []
        for entry in entries:
            name = entry.name.casefold()
            description = entry.description.casefold()
            score = 0
            if name and name in query:
                score += 100
            entry_words = set(re.findall(r"[a-z0-9][a-z0-9_-]{2,}", f"{name} {description}"))
            score += len(query_words & entry_words) * 5
            shared_jp = query_jp & _japanese_trigrams(description)
            if len(shared_jp) >= 4:
                score += len(shared_jp)
            if score > 0:
                scored.append((score, entry.name.casefold(), entry))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(item[2] for item in scored[:max_skills])

    def public_payload(self) -> dict[str, object]:
        entries = self.index()
        return {
            "count": len(entries),
            "skills": [
                {"name": entry.name, "description": entry.description}
                for entry in entries
            ],
        }

    @staticmethod
    def _parse_front_matter(directory_name: str, raw: str) -> tuple[str, str, int]:
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
        return name, description, closing_index

    @staticmethod
    def _parse(directory_name: str, raw: str) -> SkillDocument:
        text = raw.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        name, description, closing_index = SkillRegistry._parse_front_matter(directory_name, text)
        body = "\n".join(lines[closing_index + 1 :]).strip()
        if not body:
            raise ValueError("SKILL.md body must not be empty")

        return SkillDocument(name=name, description=description, body=body)


def _japanese_trigrams(text: str) -> set[str]:
    chunks = re.findall(r"[ぁ-んァ-ヶ一-龯々ー]{3,}", text)
    return {
        chunk[index : index + 3]
        for chunk in chunks
        for index in range(len(chunk) - 2)
    }
