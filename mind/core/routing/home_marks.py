"""Home-only marks for individual text fragments; unknown always remains local."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path


def fingerprint(text: str) -> str:
    return hashlib.sha256(text.strip().encode('utf-8')).hexdigest()


def sentences(text: str) -> tuple[str, ...]:
    return tuple(part for part in re.split(r'(?<=[。！？!?])|(?<=\n)', text) if part.strip())


class HomeMarks:
    def __init__(self, data_dir: Path | str) -> None:
        self.path = Path(data_dir) / 'home_marks.json'
        try:
            self.marks = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(self.marks, dict):
                self.marks = {}
        except (OSError, ValueError):
            self.marks = {}

    def home(self, text: str) -> bool:
        return any(self.marks.get(fingerprint(part)) is not False for part in sentences(text))

    def unmarked(self, texts: list[str]) -> list[str]:
        return [part for text in texts for part in sentences(text)
                if self.marks.get(fingerprint(part)) is None]

    def safe_only(self, text: str) -> str:
        return ''.join(part for part in sentences(text)
                       if self.marks.get(fingerprint(part)) is False)

    def _save(self, updated: dict[str, bool | None]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as f:
                json.dump(updated, f, ensure_ascii=False, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            tmp.unlink(missing_ok=True)
        self.marks = updated

    def mark(self, parts: list[str], home_indices: list[int]) -> None:
        if any(type(i) is not int or i < 1 or i > len(parts) for i in home_indices):
            raise ValueError('invalid home index')
        updated = dict(self.marks)
        for i, part in enumerate(parts, 1):
            updated[fingerprint(part)] = i in home_indices
        self._save(updated)

    def record_unmarked(self, text: str) -> None:
        updated = dict(self.marks)
        for part in sentences(text):
            updated[fingerprint(part)] = None
        if updated != self.marks:
            self._save(updated)
