from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any


class WorldMemoryError(RuntimeError):
    pass


class WorldMemoryService:
    PUBLIC_KINDS = {"say", "resident_say"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.episodes_root = self.root / "world_memory" / "episodes"
        self.episodes_root.mkdir(parents=True, exist_ok=True)

    def record_public_entry(self, entry: dict[str, Any]) -> None:
        if entry.get("kind") not in self.PUBLIC_KINDS:
            return
        session_id = entry.get("session")
        sender = entry.get("from")
        text = entry.get("text")
        ts = entry.get("ts")
        if not all(isinstance(value, str) and value for value in (session_id, sender, text, ts)):
            raise WorldMemoryError("public entry is missing required fields")

        path = self._episode_path(session_id)
        fingerprint_source = "\x1f".join([
            session_id,
            str(entry.get("kind")),
            sender,
            text,
            str(entry.get("request_id", "")),
        ])
        marker = f"<!-- entry:{sha256(fingerprint_source.encode('utf-8')).hexdigest()[:20]} -->"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if marker in existing:
            return

        if not existing:
            existing = (
                f"# World Memory Episode\n\n"
                f"session_id: {session_id}\n"
                f"episode_id: {session_id}-E001\n\n"
                "## 公開会話\n"
            )
        label = "Master" if sender == "master" else sender
        clean_text = " ".join(text.split())[:240]
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(existing.rstrip() + "\n")
            handle.write(f"{marker}\n- {ts} {label}: {clean_text}\n")

    def forget_session(self, session_id: str) -> int:
        self._validate_session_id(session_id)
        deleted = 0
        for path in self.episodes_root.glob(f"{session_id}-E*.md"):
            path.unlink(missing_ok=True)
            deleted += 1
        return deleted

    def episodes_for_session(self, session_id: str) -> list[Path]:
        self._validate_session_id(session_id)
        return sorted(self.episodes_root.glob(f"{session_id}-E*.md"))

    def _episode_path(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        return self.episodes_root / f"{session_id}-E001.md"

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id or any(character in session_id for character in '/\\:*?"<>|'):
            raise WorldMemoryError("invalid session id")
