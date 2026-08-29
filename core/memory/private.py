from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class PrivateMemoryError(RuntimeError):
    pass


class PrivateMemoryService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.residents_root = self.root / "residents"

    def append_whisper(
        self,
        resident_name: str,
        *,
        session_id: str,
        sender: str,
        recipient: str,
        text: str,
        request_id: str | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise PrivateMemoryError("whisper text must not be empty")
        private_dir = self._private_dir(resident_name)
        private_dir.mkdir(parents=True, exist_ok=True)
        entry: dict[str, Any] = {
            "ts": ts or datetime.now().astimezone().isoformat(timespec="seconds"),
            "session": session_id,
            "from": sender,
            "to": recipient,
            "text": cleaned,
        }
        if request_id is not None:
            entry["request_id"] = request_id
        whispers_path = private_dir / "whispers.jsonl"
        with whispers_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._refresh_context(resident_name)
        return entry

    def recent_whispers(self, resident_name: str, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        path = self._private_dir(resident_name) / "whispers.jsonl"
        if not path.exists():
            return []
        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                entries.append(parsed)
        return entries[-limit:]

    def context_for_brain(self, resident_name: str, session_id: str) -> dict[str, Any]:
        private_dir = self._private_dir(resident_name)
        context_path = private_dir / "context.md"
        context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
        recent = self.recent_whispers(resident_name, 20)
        current_session = [entry for entry in recent if entry.get("session") == session_id]
        return {
            "private_context": context_text,
            "recent_whispers": recent,
            "current_whisper_history": current_session,
        }

    def _refresh_context(self, resident_name: str) -> None:
        entries = self.recent_whispers(resident_name, 12)
        lines = ["# Private Context", "", "## 前回までのWhisper"]
        if not entries:
            lines.append("- まだWhisperはありません。")
        else:
            for entry in entries:
                sender = entry.get("from") if isinstance(entry.get("from"), str) else "?"
                recipient = entry.get("to") if isinstance(entry.get("to"), str) else "?"
                text = entry.get("text") if isinstance(entry.get("text"), str) else ""
                ts = entry.get("ts") if isinstance(entry.get("ts"), str) else ""
                excerpt = " ".join(text.split())[:240]
                lines.append(f"- {ts} {sender} → {recipient}: {excerpt}")
        payload = "\n".join(lines).strip() + "\n"
        if len(payload) > 4096:
            payload = payload[-4096:]
            payload = "# Private Context\n\n## 前回までのWhisper\n" + payload.split("\n", 2)[-1]
        (self._private_dir(resident_name) / "context.md").write_text(payload, encoding="utf-8")

    def _private_dir(self, resident_name: str) -> Path:
        resident_dir = (self.residents_root / resident_name).resolve()
        try:
            resident_dir.relative_to(self.residents_root.resolve())
        except ValueError as exc:
            raise PrivateMemoryError("Resident path escaped residents root") from exc
        if not (resident_dir / "config.toml").is_file():
            raise PrivateMemoryError(f"Resident not found: {resident_name}")
        return resident_dir / "private"
