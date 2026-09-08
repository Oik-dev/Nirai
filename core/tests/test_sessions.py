import json
from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

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


def test_public_history_includes_resident_chat_and_excludes_whispers(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))
    session_id = manager.active_session_id
    public = manager.append_master_say("公開", "REQ-PUB")
    resident_chat = manager.append_resident_chat(session_id, "Lapan", "Kina", "住人同士の公開会話")
    secret = manager.append_master_whisper("Lapan", "秘密", "REQ-SEC")
    reply = manager.append_resident_whisper(session_id, "Lapan", "秘密の返事", "REQ-SEC")

    assert manager.history() == [public, resident_chat, secret, reply]
    assert manager.public_history(session_id) == [public, resident_chat]
    assert resident_chat["kind"] == "resident_chat"
    assert resident_chat["to"] == "Kina"
    assert "request_id" not in resident_chat
    assert manager.whisper_history(session_id, "Lapan") == [secret, reply]


def test_chat_history_index_reads_only_new_jsonl_tail_after_initial_import(tmp_path: Path) -> None:
    root = tmp_path / "chat_sessions"
    manager = SessionManager(ChatStore(root))
    session_id = manager.active_session_id
    path = root / f"{session_id}.jsonl"
    first = {
        "entry_id": "CE-OLD",
        "ts": "2026-09-01T00:00:00+09:00",
        "kind": "say",
        "from": "master",
        "text": "old",
        "session": session_id,
    }
    path.write_text(json.dumps(first, ensure_ascii=False) + "\n", encoding="utf-8")
    assert [entry["entry_id"] for entry in manager.history()] == ["CE-OLD"]

    second = {
        "entry_id": "CE-NEW",
        "ts": "2026-09-01T00:00:01+09:00",
        "kind": "say",
        "from": "master",
        "text": "new",
        "session": session_id,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(second, ensure_ascii=False) + "\n")

    assert [entry["entry_id"] for entry in manager.history()] == ["CE-OLD", "CE-NEW"]
    assert [entry["entry_id"] for entry in manager.public_history_after(session_id, "CE-OLD")] == ["CE-NEW"]
    assert (root / "entries.sqlite3").is_file()


def test_find_task_entry_uses_indexed_agent_session_lookup(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path / "chat_sessions"))
    session_id = manager.active_session_id
    manager.append_task(
        session_id,
        "Lapan",
        "work result",
        task_id="T-INDEX",
        agent_session_id="AS-INDEX",
    )

    entry = manager.find_task_entry(session_id, "AS-INDEX")

    assert entry is not None
    assert entry["task_id"] == "T-INDEX"
    assert entry["text"] == "work result"


def test_delete_session_removes_only_chat_session_and_keeps_an_active_session(tmp_path: Path) -> None:
    root = tmp_path / "chat_sessions"
    manager = SessionManager(ChatStore(root))
    first_id = manager.active_session_id
    second_id = manager.create_session()["id"]

    manager.delete_session(second_id)

    assert not manager.store.has_session(second_id)
    assert manager.active_session_id == first_id
    assert not (root / f"{second_id}.jsonl").exists()


def test_append_imports_unindexed_history_before_advancing_cursor(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    session_id = store.create_session()["id"]
    old = {"entry_id": "CE-OLD", "text": "old", "kind": "say", "from": "master"}
    path = tmp_path / f"{session_id}.jsonl"
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")

    new = store.append_entry(session_id, kind="say", sender="master", text="new")

    assert store.read_history(session_id) == [old, new]
    assert store.read_entries_after(session_id, "CE-OLD") == [new]


def test_append_after_index_failure_recovers_the_saved_raw_entry(tmp_path: Path, monkeypatch) -> None:
    store = ChatStore(tmp_path)
    session = store.create_session()
    session_id = session["id"]
    original_updated_at = session["updated_at"]
    with monkeypatch.context() as patch:
        def fail_insert(*_args, **_kwargs):
            raise sqlite3.OperationalError("simulated index failure")
        patch.setattr(store, "_insert_indexed_entry", fail_insert)
        with pytest.raises(sqlite3.OperationalError):
            store.append_entry(session_id, kind="say", sender="master", text="saved before failure")

    # Raw fsync is the durable commit barrier. Even if the derived SQLite
    # update fails and Core restarts immediately, Sidebar metadata must already
    # describe the committed message rather than the pre-send state.
    restarted = ChatStore(tmp_path)
    recovered_metadata = next(item for item in restarted.list_sessions() if item["id"] == session_id)
    assert recovered_metadata["title"] == "saved before failure"
    assert recovered_metadata["updated_at"] != original_updated_at
    assert [entry["text"] for entry in restarted.read_history(session_id)] == [
        "saved before failure",
    ]

    restarted.append_entry(session_id, kind="say", sender="master", text="after recovery")

    assert [entry["text"] for entry in restarted.read_history(session_id)] == [
        "saved before failure", "after recovery",
    ]


@pytest.mark.parametrize("tail", [b'{"text":"broken', b'{"text":"complete","kind":"say"}'])
def test_append_keeps_unterminated_tail_separate_and_index_rebuildable(tmp_path: Path, tail: bytes) -> None:
    store = ChatStore(tmp_path)
    session_id = store.create_session()["id"]
    path = tmp_path / f"{session_id}.jsonl"
    path.write_bytes(tail)
    new = store.append_entry(session_id, kind="say", sender="master", text="new")
    expected = ([json.loads(tail)] if tail.endswith(b"}") else []) + [new]
    assert store.read_history(session_id) == expected
    assert path.read_bytes().startswith(tail + b"\n")
    # Simulate loss of the derived index, without changing the raw log.
    with closing(sqlite3.connect(store.entries_db_path)) as connection, connection:
        connection.execute("DELETE FROM chat_entries")
        connection.execute("DELETE FROM chat_index_state")
    assert ChatStore(tmp_path).read_history(session_id) == expected


def test_empty_raw_log_clears_stale_index_persistently(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    session_id = store.create_session()["id"]
    store.append_entry(session_id, kind="say", sender="master", text="old")
    (tmp_path / f"{session_id}.jsonl").write_bytes(b"")
    assert store.read_history(session_id) == []
    assert ChatStore(tmp_path).read_history(session_id) == []


def test_channel_history_is_not_displaced_by_unrelated_conversations(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path))
    session_id = manager.active_session_id
    public = manager.append_master_say("public context", "REQ-PUB")
    whisper = manager.append_master_whisper("Lapan", "private context", "REQ-PRIVATE")
    for index in range(85):
        manager.append_master_whisper("Kina", f"unrelated-{index}", f"REQ-{index}")

    assert manager.public_history(session_id, limit=20) == [public]
    assert manager.whisper_history(session_id, "Lapan", limit=20) == [whisper]
    assert manager.public_history(session_id, limit=0) == []
    assert manager.whisper_history(session_id, "Lapan", limit=0) == []


def test_public_history_after_filters_unrelated_channels_before_payload_decode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    manager = SessionManager(ChatStore(tmp_path))
    session_id = manager.active_session_id
    marker = manager.append_master_say("marker", "REQ-MARKER")
    for index in range(1_000):
        manager.append_master_whisper("Kina", f"unrelated-{index}", f"REQ-U-{index}")
    expected = manager.append_resident_say(session_id, "Lapan", "public-after", "REQ-PUBLIC")

    import core.sessions.chat_store as chat_store_module

    original_loads = chat_store_module.json.loads
    decoded_payloads = 0

    def counting_loads(value, *args, **kwargs):
        nonlocal decoded_payloads
        decoded_payloads += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(chat_store_module.json, "loads", counting_loads)

    assert manager.public_history_after(session_id, marker["entry_id"]) == [expected]
    # One decode is index.json via has_session(), one is the matching public
    # payload. None of the 1,000 unrelated Whisper payloads is materialized.
    assert decoded_payloads == 2


def test_channel_history_merges_kinds_in_append_order_with_exact_limit(tmp_path: Path) -> None:
    manager = SessionManager(ChatStore(tmp_path))
    session_id = manager.active_session_id
    manager.append_master_say("old", "REQ-0")
    first = manager.append_resident_chat(session_id, "Lapan", None, "public reply")
    manager.append_master_whisper("Kina", "excluded", "REQ-PRIVATE")
    second = manager.append_holo_say("Holo reply")
    manager.append_system(session_id, "excluded system")
    third = manager.append_master_say("new", "REQ-1")

    assert manager.public_history(session_id, limit=3) == [first, second, third]
    outgoing = manager.append_master_whisper("Lapan", "hi", "REQ-L")
    incoming = manager.append_resident_whisper(session_id, "Lapan", "hello", "REQ-L")
    assert manager.whisper_history(session_id, "Lapan", limit=2) == [outgoing, incoming]
    assert manager.whisper_history(session_id, "Lapan", limit=1) == [incoming]


def test_legacy_index_checkpoint_is_rebuilt_once_from_raw_history(tmp_path: Path) -> None:
    store = ChatStore(tmp_path)
    session_id = store.create_session()["id"]
    first = store.append_entry(session_id, kind="say", sender="master", text="old")
    second = store.append_entry(session_id, kind="say", sender="master", text="new")
    # Reproduce a pre-fix database: checkpoint says EOF but the old entry is absent.
    with closing(sqlite3.connect(store.entries_db_path)) as connection, connection:
        connection.execute("DELETE FROM chat_entries WHERE entry_id=?", (first["entry_id"],))
        connection.execute("PRAGMA user_version=0")
    assert ChatStore(tmp_path).read_history(session_id) == [first, second]
    # Opening an upgraded index must not throw away its incremental checkpoint.
    with closing(sqlite3.connect(store.entries_db_path)) as connection, connection:
        checkpoint = connection.execute("SELECT indexed_bytes FROM chat_index_state").fetchall()
    ChatStore(tmp_path)
    with closing(sqlite3.connect(store.entries_db_path)) as connection, connection:
        assert connection.execute("SELECT indexed_bytes FROM chat_index_state").fetchall() == checkpoint
