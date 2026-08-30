from pathlib import Path

from core.memory.private import PrivateMemoryService
from core.memory.world import WorldMemoryService


def make_resident(root: Path, name: str = "Lapan") -> None:
    resident_dir = root / "residents" / name
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "config.toml").write_text('brain = "codex"\n', encoding="utf-8")


def test_private_memory_keeps_old_whisper_without_date_expiry(tmp_path: Path) -> None:
    make_resident(tmp_path)
    memory = PrivateMemoryService(tmp_path)
    memory.append_whisper(
        "Lapan",
        session_id="S-OLD",
        sender="master",
        recipient="Lapan",
        text="30日前の秘密",
        request_id="REQ-OLD",
        ts="2026-07-29T12:00:00+09:00",
    )

    context = memory.context_for_brain("Lapan", "S-NEW")

    assert any(entry["text"] == "30日前の秘密" for entry in context["recent_whispers"])
    assert "30日前の秘密" in context["private_context"]


def test_world_memory_records_only_public_entries_once(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    public = {
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "公開の話",
        "session": "S-20260828-001",
        "request_id": "REQ-PUBLIC",
    }
    resident_chat = {
        "ts": "2026-08-28T12:00:30+09:00",
        "kind": "resident_chat",
        "from": "Lapan",
        "to": "Kina",
        "text": "住人同士の公開会話",
        "session": "S-20260828-001",
    }
    whisper = {
        "ts": "2026-08-28T12:01:00+09:00",
        "kind": "whisper",
        "from": "master",
        "to": "Lapan",
        "text": "秘密の話",
        "session": "S-20260828-001",
        "request_id": "REQ-PRIVATE",
    }

    memory.record_public_entry(public)
    memory.record_public_entry(public)
    memory.record_public_entry(resident_chat)
    memory.record_public_entry(whisper)

    paths = memory.episodes_for_session("S-20260828-001")
    assert len(paths) == 1
    text = paths[0].read_text(encoding="utf-8")
    assert text.count("公開の話") == 1
    assert text.count("住人同士の公開会話") == 1
    assert "秘密の話" not in text


def test_world_memory_forget_removes_episode_only(tmp_path: Path) -> None:
    memory = WorldMemoryService(tmp_path)
    memory.record_public_entry({
        "ts": "2026-08-28T12:00:00+09:00",
        "kind": "resident_say",
        "from": "Lapan",
        "text": "覚えておく公開会話",
        "session": "S-20260828-001",
        "request_id": "REQ-1",
    })

    assert memory.forget_session("S-20260828-001") == 1
    assert memory.episodes_for_session("S-20260828-001") == []
