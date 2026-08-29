import json
from pathlib import Path

from core.sessions.chat_store import ChatStore
from core.sessions.manager import SessionManager


def test_first_run_creates_an_active_session(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))

    assert manager.active_session_id.startswith("S-")
    sessions = manager.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["id"] == manager.active_session_id
    assert sessions[0]["title"] == "新しいチャット"


def test_create_and_select_session_persist_in_index(tmp_path: Path) -> None:
    root = tmp_path / "chat_sessions"
    manager = SessionManager(ChatStore(root))
    first_id = manager.active_session_id

    second = manager.create_session()
    assert manager.active_session_id == second["id"]
    assert second["id"] != first_id

    manager.select_session(first_id)
    assert manager.active_session_id == first_id

    reloaded = SessionManager(ChatStore(root))
    listed_ids = {item["id"] for item in reloaded.list_sessions()}
    assert listed_ids == {first_id, second["id"]}


def test_history_request_on_empty_session_returns_empty_list(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))

    assert manager.history(limit=50) == []


def test_history_cursor_pages_existing_second_precision_entries_without_gaps(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))
    session_id = manager.active_session_id
    entries = [
        {
            "ts": "2026-08-29T00:00:00+09:00",
            "kind": "say",
            "from": "master",
            "text": f"message-{index:02d}",
            "session": session_id,
            "request_id": f"REQ-{index:02d}",
        }
        for index in range(75)
    ]
    path = tmp_path / "chat_sessions" / f"{session_id}.jsonl"
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
    )

    latest, before = manager.history_page(limit=50)
    older, next_before = manager.history_page(before=before, limit=50)

    assert before == "25"
    assert next_before is None
    assert len(latest) == 50
    assert len(older) == 25
    assert older[-1]["text"] == "message-24"
    assert latest[0]["text"] == "message-25"
    assert {entry["request_id"] for entry in older}.isdisjoint(
        entry["request_id"] for entry in latest
    )


def test_master_say_is_persisted_with_request_id_and_updates_title(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))

    entry = manager.append_master_say("  海の話をしよう  ", "REQ-1")

    assert entry["kind"] == "say"
    assert entry["from"] == "master"
    assert entry["text"] == "海の話をしよう"
    assert entry["request_id"] == "REQ-1"
    assert entry["session"] == manager.active_session_id
    assert manager.history() == [entry]
    assert manager.list_sessions()[0]["title"] == "海の話をしよう"


def test_public_history_excludes_whispers(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))
    session_id = manager.active_session_id
    public = manager.append_master_say("公開", "REQ-PUB")
    secret = manager.append_master_whisper("Lapan", "秘密", "REQ-SEC")
    reply = manager.append_resident_whisper(session_id, "Lapan", "秘密の返事", "REQ-SEC")

    assert manager.history() == [public, secret, reply]
    assert manager.public_history(session_id) == [public]
    assert manager.whisper_history(session_id, "Lapan") == [secret, reply]


def test_delete_session_removes_only_chat_session_and_keeps_an_active_session(tmp_path: Path) -> None:
    root = tmp_path / "chat_sessions"
    manager = SessionManager(ChatStore(root))
    first_id = manager.active_session_id
    second_id = manager.create_session()["id"]

    manager.delete_session(second_id)

    assert not manager.store.has_session(second_id)
    assert manager.active_session_id == first_id
    assert not (root / f"{second_id}.jsonl").exists()
