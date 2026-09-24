"""Regression coverage for the post-refactor maintenance pass."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import core.agents.cursor_credentials as cursor_credentials
from core.agents.cursor_acp import CursorAcpAdapter
from core.agents.base import AgentRuntimeError, AgentRuntimeUnavailableError
from core.agents.safety import AgentWorkspacePolicy
from core.agents.store import AgentSessionStore, AgentSessionStoreError


def _cursor_home_fixture(tmp_path: Path, *, persistent: bool):
    source = tmp_path / "runtime/cursor_profile/.cursor/agent-cli-state.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"token": "synthetic-test-only"}', encoding="utf-8")
    adapter = CursorAcpAdapter(AgentWorkspacePolicy(tmp_path, ("runtime/workspace",)))
    stable_key = "conversation-test" if persistent else None
    if persistent:
        digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()[:32]
        target = tmp_path / "runtime/cursor_conversation_homes" / f"CV-{digest}"
        target.mkdir(parents=True)
        (target / "native-session.json").write_text("keep continuity", encoding="utf-8")
    else:
        target = tmp_path / "runtime/cursor_agent_homes/AS-PREPARE"
    return adapter, stable_key, target, source


@pytest.mark.parametrize(
    ("persistent", "failure"),
    [
        (False, "permission_rules"),
        (True, "config_write"),
    ],
)
def test_cursor_home_late_prepare_failure_scrubs_auth(tmp_path: Path, monkeypatch, persistent, failure) -> None:
    adapter, stable_key, target, source = _cursor_home_fixture(tmp_path, persistent=persistent)
    monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda path: None)
    if failure == "permission_rules":
        def fail_rules(*args, **kwargs):
            raise OSError("injected late prepare failure")
        monkeypatch.setattr(adapter, "_cursor_permission_denies", fail_rules)
    else:
        original_write = Path.write_text
        def fail_config(path, *args, **kwargs):
            if path.name == "cli-config.json":
                raise OSError("injected late prepare failure")
            return original_write(path, *args, **kwargs)
        monkeypatch.setattr(Path, "write_text", fail_config)

    with pytest.raises(OSError, match="injected late prepare failure"):
        adapter._prepare_cursor_home("AS-PREPARE", stable_key=stable_key)

    assert source.is_file()
    assert not (target / ".cursor/agent-cli-state.json").exists()
    if persistent:
        assert (target / "native-session.json").read_text(encoding="utf-8") == "keep continuity"
    else:
        assert not target.exists()


def test_cursor_home_late_failure_falls_back_to_removing_context(tmp_path: Path, monkeypatch) -> None:
    adapter, stable_key, target, source = _cursor_home_fixture(tmp_path, persistent=True)
    monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda path: None)
    original_remove = adapter._remove_cursor_auth_copy

    def fail_scrub(home):
        if (home / ".cursor/agent-cli-state.json").exists():
            raise AgentRuntimeError("injected scrub failure")
        original_remove(home)

    def fail_rules(*args, **kwargs):
        raise OSError("injected late prepare failure")

    monkeypatch.setattr(adapter, "_remove_cursor_auth_copy", fail_scrub)
    monkeypatch.setattr(adapter, "_cursor_permission_denies", fail_rules)

    with pytest.raises(OSError, match="injected late prepare failure"):
        adapter._prepare_cursor_home("AS-PREPARE", stable_key=stable_key)

    assert not target.exists()
    assert source.is_file()


@pytest.mark.parametrize(
    ("persistent", "failure"),
    [
        (False, "missing_auth"),
        (True, "partial_copy"),
        (True, "auth_acl"),
    ],
)
def test_cursor_home_early_failure_keeps_error_contract_and_scrubs_auth(
    tmp_path: Path, monkeypatch, persistent, failure
) -> None:
    adapter, stable_key, target, source = _cursor_home_fixture(tmp_path, persistent=persistent)
    monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda path: None)
    if failure == "missing_auth":
        monkeypatch.setattr(cursor_credentials, "_cursor_auth_state_source", lambda root: None)
        message = "Cursor login state is unavailable"
    elif failure == "partial_copy":
        def fail_copy(source, destination):
            destination.write_bytes(b"partial synthetic auth")
            raise OSError("injected copy failure")
        monkeypatch.setattr(cursor_credentials.shutil, "copyfile", fail_copy)
        message = "Cursor authentication could not be isolated"
    else:
        def fail_acl(path):
            raise AgentRuntimeUnavailableError("injected ACL failure")
        monkeypatch.setattr(adapter, "_restrict_auth_permissions", fail_acl)
        message = "Cursor authentication could not be isolated"

    with pytest.raises(AgentRuntimeUnavailableError, match=message):
        adapter._prepare_cursor_home("AS-PREPARE", stable_key=stable_key)

    assert source.is_file()
    assert not (target / ".cursor/agent-cli-state.json").exists()
    assert (target / "native-session.json").exists() is persistent
    if not persistent:
        assert not target.exists()


def test_cursor_home_prepare_reports_cleanup_failure(tmp_path: Path, monkeypatch) -> None:
    adapter, stable_key, target, source = _cursor_home_fixture(tmp_path, persistent=False)
    monkeypatch.setattr(adapter, "_restrict_auth_permissions", lambda path: None)

    def fail_rules(*args, **kwargs):
        raise OSError("injected prepare failure")

    def fail_cleanup(home):
        raise AgentRuntimeError("injected cleanup failure")

    monkeypatch.setattr(adapter, "_cursor_permission_denies", fail_rules)
    monkeypatch.setattr(adapter, "_cleanup_cursor_home", fail_cleanup)

    with pytest.raises(AgentRuntimeError, match="injected cleanup failure") as caught:
        adapter._prepare_cursor_home("AS-PREPARE", stable_key=stable_key)
    assert isinstance(caught.value.__context__, OSError)


def _event_log(tmp_path: Path, raw: bytes):
    store = AgentSessionStore(tmp_path)
    path = store.sessions_root / "AS-TAIL/events.jsonl"
    path.parent.mkdir()
    path.write_bytes(raw)
    return store, path


@pytest.mark.parametrize(
    ("limit", "ending"),
    [
        (1, b""),
        (3, b"\n"),
        (20, b"\r\n"),
    ],
)
def test_event_tail_keeps_complete_utf8_events_across_blocks(tmp_path: Path, ending: bytes, limit: int) -> None:
    events = [{"seq": seq, "text": "日本語🦉" * 17000 + str(seq)} for seq in range(1, 6)]
    raw = b"\r\n".join(json.dumps(event, ensure_ascii=False).encode("utf-8") for event in events) + ending
    store, path = _event_log(tmp_path, raw)

    assert store.read_event_tail("AS-TAIL", limit=limit) == events[-limit:]
    assert path.read_bytes() == raw


def test_event_tail_opens_log_once_for_large_window(tmp_path: Path, monkeypatch) -> None:
    events = [{"seq": seq, "text": "x" * 100000} for seq in range(6)]
    raw = b"\n".join(json.dumps(event).encode() for event in events) + b"\n"
    store, path = _event_log(tmp_path, raw)
    original_open = Path.open
    reads = []

    def track_open(candidate, mode="r", *args, **kwargs):
        if candidate == path and mode == "rb":
            reads.append(candidate)
        return original_open(candidate, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", track_open)
    assert store.read_event_tail("AS-TAIL", limit=5) == events[-5:]
    assert len(reads) == 1


def test_event_tail_repairs_only_incomplete_final_record(tmp_path: Path) -> None:
    prefix = b'{"seq": 1}\n{"seq": 2}\n'
    store, path = _event_log(tmp_path, prefix + b'{"seq": 3, "text": "\xe3')

    assert store.read_event_tail("AS-TAIL", limit=2) == [{"seq": 1}, {"seq": 2}]
    assert path.read_bytes() == prefix


def test_event_tail_rejects_corruption_before_final_record(tmp_path: Path) -> None:
    raw = b'{"seq": 1}\nBROKEN\n{"seq": 3}\n'
    store, path = _event_log(tmp_path, raw)

    with pytest.raises(AgentSessionStoreError, match="malformed before its tail"):
        store.read_event_tail("AS-TAIL", limit=3)
    assert path.read_bytes() == raw
