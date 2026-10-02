"""会話の生ログ（core/lifelog.py）と、会話帳簿（SessionStore）との一致。

守るもの：会話の原文がイデアの生ログに必ず残ること。消えるのはMasterが明示したときだけ。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.idea import RESIDENT_NAME
from mind.core.lifelog import MASTER, ConversationLog
from mind.core.memory.session_store import SessionStore


def _lines(directory: Path) -> list[dict]:
    out: list[dict] = []
    for path in sorted(directory.glob("*.jsonl")):
        out += [json.loads(raw) for raw in path.read_text(encoding="utf-8").splitlines() if raw]
    return out


@pytest.fixture
def log(tmp_path: Path) -> ConversationLog:
    return ConversationLog(tmp_path / "conversation")


@pytest.fixture
def store(tmp_path: Path, log: ConversationLog) -> SessionStore:
    s = SessionStore(tmp_path / "session.db", conversation_log=log)
    s.create_session("s1")
    return s


def test_lines_go_to_the_japan_date_file(log: ConversationLog) -> None:
    # UTC 16:00 は日本時間で翌日の 01:00
    log.append(ts="2026-07-30T16:00:00+00:00", session="s1", speaker=MASTER, text="こんばんは")
    log.append(ts="2026-07-30T14:00:00+00:00", session="s1", speaker=MASTER, text="まだ夜")
    assert sorted(p.name for p in log.directory.glob("*.jsonl")) == ["2026-07-30.jsonl", "2026-07-31.jsonl"]


def test_file_is_utf8_with_lf_line_ends(log: ConversationLog) -> None:
    log.append(ts="2026-07-30T16:00:00+00:00", session="s1", speaker=MASTER, text="宮古島の海")
    raw = (log.directory / "2026-07-31.jsonl").read_bytes()
    assert b"\r" not in raw
    assert "宮古島の海" in raw.decode("utf-8")


def test_add_missing_is_idempotent(log: ConversationLog) -> None:
    lines = [
        {"ts": "2026-07-30T16:00:00+00:00", "session": "s1", "speaker": MASTER, "text": "a"},
        {"ts": "2026-07-30T16:00:01+00:00", "session": "s1", "speaker": "Serina", "text": "b"},
    ]
    assert log.add_missing(lines) == 2
    assert log.add_missing(lines) == 0
    assert [line["text"] for line in _lines(log.directory)] == ["a", "b"]


def test_add_history_writes_the_log_with_speaker_names(store: SessionStore, log: ConversationLog) -> None:
    store.add_history("s1", "user", "調子はどうかな")
    store.add_history("s1", "assistant", "うん、わたしは大丈夫だよ。")
    lines = _lines(log.directory)
    assert [(line["speaker"], line["text"]) for line in lines] == [
        (MASTER, "調子はどうかな"),
        (RESIDENT_NAME, "うん、わたしは大丈夫だよ。"),
    ]
    history = store.get_session_history("s1")
    assert [line["ts"] for line in lines] == [row["ts"] for row in history]


def test_conversation_continues_when_the_log_cannot_be_written(store: SessionStore, monkeypatch) -> None:
    def broken(**_kwargs):  # noqa: ANN003, ANN202
        raise OSError("disk full")

    monkeypatch.setattr(store.conversation_log, "append", broken)
    store.add_history("s1", "user", "聞こえる？")
    assert [row["content"] for row in store.get_session_history("s1")] == ["聞こえる？"]


def test_sync_fills_what_the_log_missed(store: SessionStore, log: ConversationLog, monkeypatch) -> None:
    store.add_history("s1", "user", "一つめ")
    with monkeypatch.context() as m:
        m.setattr(store.conversation_log, "append", lambda **_kw: (_ for _ in ()).throw(OSError()))
        store.add_history("s1", "assistant", "二つめ（書き損ね）")
    assert [line["text"] for line in _lines(log.directory)] == ["一つめ"]
    assert store.sync_conversation_log() == 1
    assert store.sync_conversation_log() == 0
    assert [line["text"] for line in _lines(log.directory)] == ["一つめ", "二つめ（書き損ね）"]


def test_sync_keeps_lines_the_ledger_no_longer_has(store: SessionStore, log: ConversationLog) -> None:
    """帳簿の古いアーカイブが消えても（保守の掃除）、生ログは残る。"""
    store.add_history("s1", "user", "残してね")
    store.archive_session_history("s1")
    store.purge_archived_older_than(-1)
    assert store.sync_conversation_log() == 0
    assert [line["text"] for line in _lines(log.directory)] == ["残してね"]


def test_master_deleting_a_message_removes_it_from_the_log(store: SessionStore, log: ConversationLog) -> None:
    keep = store.add_history("s1", "user", "残す")
    drop = store.add_history("s1", "user", "消す")
    store.delete_message(drop)
    assert [line["text"] for line in _lines(log.directory)] == ["残す"]
    assert store.get_message(keep) is not None


def test_master_deleting_a_session_removes_all_its_lines(store: SessionStore, log: ConversationLog) -> None:
    store.create_session("s2")
    store.add_history("s1", "user", "s1の発言")
    store.add_history("s2", "user", "s2の発言")
    store.add_history("s2", "assistant", "s2の返事")
    store.delete_session("s2")
    assert [line["text"] for line in _lines(log.directory)] == ["s1の発言"]


def test_failed_log_removal_keeps_the_message_in_the_ledger(store: SessionStore, monkeypatch) -> None:
    """生ログから消せなかったら、帳簿からも消さない（消えたと誤認させない）。"""
    mid = store.add_history("s1", "user", "消したい")

    def broken(**_kwargs):  # noqa: ANN003, ANN202
        raise OSError("locked")

    monkeypatch.setattr(store.conversation_log, "remove", broken)
    with pytest.raises(OSError):
        store.delete_message(mid)
    assert store.get_message(mid) is not None
