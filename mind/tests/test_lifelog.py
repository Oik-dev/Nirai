"""会話の生ログ（core/lifelog.py）と、会話帳簿（SessionStore）との一致。

守るもの：会話の原文がイデアの生ログに必ず残ること。消えるのはMasterが明示したときだけで、そのときも
行番号は変わらない（記憶のページと気持ちの記録は、会話を「日のファイルと行番号」で指すため）。書いた場所は、読むときの
行番号と同じに返す。思い出したことの記録（RecallLog）と気持ちの記録（FeelingLog）も見る。気持ちの記録は、Masterが
会話を消したら言葉だけを消し、数は残す。書きかけで壊れた行は読まない。
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
from datetime import datetime, timezone

from mind.core.lifelog import MASTER, ConversationLog, FeelingLog, RecallLog, positions_of, read_conversation, refs_of
from mind.core.memory.session_store import SessionStore


def _lines(directory: Path) -> list[dict]:
    """消した印の行を除いた、今ある発言。"""
    return [line for line in _rows(directory) if not line.get("deleted")]


def _rows(directory: Path) -> list[dict]:
    """ファイルの行そのもの（消した印の行も含む）。"""
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
    keep = store.add_history("s1", "user", "残す").id
    drop = store.add_history("s1", "user", "消す").id
    after = store.add_history("s1", "assistant", "あとの発言").id
    row = store.delete_message(drop)
    assert [line["text"] for line in _lines(log.directory)] == ["残す", "あとの発言"]
    assert store.get_message(keep) is not None and store.get_message(after) is not None
    # 本文は消え、印の行が残る。あとの発言の行番号は変わらない
    rows = _rows(log.directory)
    assert len(rows) == 3 and rows[1]["deleted"] is True and "text" not in rows[1]
    day = next(log.directory.glob("*.jsonl")).stem
    assert row["erased"] == [(day, 2)]
    assert [(line.no, line.text) for line in read_conversation(log.directory)] == [(1, "残す"), (3, "あとの発言")]


def test_master_deleting_a_session_removes_all_its_lines(store: SessionStore, log: ConversationLog) -> None:
    store.create_session("s2")
    store.add_history("s1", "user", "s1の発言")
    store.add_history("s2", "user", "s2の発言")
    store.add_history("s2", "assistant", "s2の返事")
    result = store.delete_session("s2")
    assert [line["text"] for line in _lines(log.directory)] == ["s1の発言"]
    assert len(result["erased"]) == 2


def test_deleted_lines_are_not_brought_back_by_sync(store: SessionStore, log: ConversationLog) -> None:
    """消した印の行は、帳簿から書き足すときにも「もうある」とは数えない（消した発言は帳簿にもない）。"""
    drop = store.add_history("s1", "user", "消す").id
    store.delete_message(drop)
    assert store.sync_conversation_log() == 0
    assert _lines(log.directory) == []


def test_recall_log_keeps_when_each_page_was_remembered(tmp_path: Path) -> None:
    recall = RecallLog(tmp_path / "recall")
    first = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)  # 日本時間では11月
    recall.append(ts=first, page="ep-a", activation=2.5, vivid=True, intent=False)
    recall.append(ts=second, page="ep-a", activation=1.6, vivid=False, intent=True)
    assert recall.times() == {"ep-a": [first, second]}
    assert sorted(p.name for p in (tmp_path / "recall").glob("*.jsonl")) == ["2026-10.jsonl", "2026-11.jsonl"]


def test_failed_log_removal_keeps_the_message_in_the_ledger(store: SessionStore, monkeypatch) -> None:
    """生ログから消せなかったら、帳簿からも消さない（消えたと誤認させない）。"""
    mid = store.add_history("s1", "user", "消したい").id

    def broken(**_kwargs):  # noqa: ANN003, ANN202
        raise OSError("locked")

    monkeypatch.setattr(store.conversation_log, "remove", broken)
    with pytest.raises(OSError):
        store.delete_message(mid)
    assert store.get_message(mid) is not None


def test_ref_delete_does_not_resurrect_when_log_rewrite_fails(store: SessionStore, log: ConversationLog, monkeypatch) -> None:
    """ref削除は帳簿を先に消す。生ログの置換に失敗しても、次回syncで本文を復活させない。"""
    recorded = store.add_history("s1", "user", "消したい")
    assert recorded.line is not None

    def broken(_day_file, _no):  # noqa: ANN001, ANN202
        raise OSError("locked")

    monkeypatch.setattr(store.conversation_log, "remove_at", broken)
    with pytest.raises(OSError):
        store.delete_conversation_position(*recorded.line)

    assert store.get_message(recorded.id) is None
    assert store.sync_conversation_log() == 0
    assert [line["text"] for line in _lines(log.directory)] == ["消したい"]


def test_add_history_tells_where_it_wrote_with_the_reading_line_numbers(store: SessionStore, log: ConversationLog) -> None:
    said = store.add_history("s1", "user", "ただいま")
    log_path = next(log.directory.glob("*.jsonl"))
    with log_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n")  # 空行も行番号に数える（読むときと同じ）
    answered = store.add_history("s1", "assistant", "おかえり")
    lines = read_conversation(log.directory)
    assert [(line.day_file, line.no) for line in lines] == [said.line, answered.line]
    assert said.line[1] == 1 and answered.line[1] == 3
    assert positions_of(refs_of([said.line, answered.line])) == {said.line, answered.line}


def test_add_history_without_the_log_has_no_line(store: SessionStore, monkeypatch) -> None:
    monkeypatch.setattr(store.conversation_log, "append", lambda **_kw: (_ for _ in ()).throw(OSError()))
    recorded = store.add_history("s1", "user", "聞こえる？")
    assert recorded.line is None and store.get_message(recorded.id) is not None


def _feeling_row(ts: str, source: list[str], words: str) -> dict:
    return {"ts": ts, "kind": "turn", "source": source, "feeling": words,
            "evaluation": {"valence": "うれしい", "arousal": "少し動いた", "distance": "近づいた", "master_state": "眠そう"},
            "after": {"fast_valence": 0.1, "fast_arousal": 0.0, "slow_valence": 0.0, "slow_arousal": 0.0, "connection": 0.8}}


def test_feeling_log_reads_in_order_and_forgets_only_words(tmp_path: Path) -> None:
    feelings = FeelingLog(tmp_path / "feeling")
    feelings.append(_feeling_row("2026-10-04T12:00:00+00:00", ["lifelog/conversation/2026-10-04.jsonl#1-2"], "うれしい"))
    feelings.append(_feeling_row("2026-10-04T16:00:00+00:00", ["lifelog/conversation/2026-10-05.jsonl#1-2"], "照れた"))
    assert sorted(p.name for p in (tmp_path / "feeling").glob("*.jsonl")) == ["2026-10-04.jsonl", "2026-10-05.jsonl"]
    assert [row["feeling"] for row in feelings.rows()] == ["うれしい", "照れた"]
    assert [row["feeling"] for row in feelings.newest_first()] == ["照れた", "うれしい"]
    assert [row["feeling"] for row in feelings.rows(days={"2026-10-04"})] == ["うれしい"]

    assert feelings.forget([("2026-10-05", 2)]) == 1
    assert feelings.forget([("2026-10-05", 2)]) == 0  # もう消してある
    forgotten = next(feelings.newest_first())
    assert forgotten["feeling"] == "" and forgotten["evaluation"]["master_state"] == "" and forgotten["forgotten"]
    assert forgotten["evaluation"]["valence"] == "うれしい" and forgotten["after"]["connection"] == 0.8
    assert next(feelings.rows())["feeling"] == "うれしい"


def test_feeling_log_skips_a_half_written_line(tmp_path: Path) -> None:
    feelings = FeelingLog(tmp_path / "feeling")
    feelings.append(_feeling_row("2026-10-04T12:00:00+00:00", [], "うれしい"))
    with (tmp_path / "feeling" / "2026-10-04.jsonl").open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-10-04T12:05')  # 電源断で書きかけ
    assert [row["feeling"] for row in feelings.newest_first()] == ["うれしい"]
    feelings.append(_feeling_row("2026-10-04T12:10:00+00:00", [], "次のターン"))  # 書きかけにくっつかない
    assert [row["feeling"] for row in feelings.rows()] == ["うれしい", "次のターン"]


def test_a_conversation_line_after_a_half_written_one_gets_its_own_number(log: ConversationLog) -> None:
    log.append(ts="2026-07-30T16:00:00+00:00", session="s1", speaker=MASTER, text="一つめ")
    path = next(log.directory.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-30T16:01')  # 電源断で書きかけ
    day, no = log.append(ts="2026-07-30T16:02:00+00:00", session="s1", speaker=MASTER, text="三つめ")
    assert no == 3 and path.read_text(encoding="utf-8").splitlines()[2].endswith('"三つめ"}')
