"""会話の生ログ（core/lifelog.py）。

守るもの：会話の原文がイデアの生ログに必ず残ること。消えるのはMasterが明示したときだけで、そのときも
行番号は変わらない（記憶のページと気持ちの記録は、会話を「日のファイルと行番号」で指すため）。書いた場所は、読むときの
行番号と同じに返す。新しい行は session を持たず、古い行（ChatGPT時代の chatgpt-…、帳簿があったころの s_…）はそのまま読める。
思い出したことの記録（RecallLog）と気持ちの記録（FeelingLog）も見る。気持ちの記録は、Masterが会話を消したら言葉だけを消し、
数は残す。書きかけで壊れた行（電源断など）は、どの記録でも読まずに飛ばし、行番号には数える。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.lifelog import MASTER, ConversationLog, FeelingLog, RecallLog, positions_of, read_conversation, refs_of
from mind.core.memory.structure import runs
from mind.memory_test.record import conversation_source


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


def test_lines_go_to_the_japan_date_file(log: ConversationLog) -> None:
    # UTC 16:00 は日本時間で翌日の 01:00
    log.append(ts="2026-07-30T16:00:00+00:00", speaker=MASTER, text="こんばんは")
    log.append(ts="2026-07-30T14:00:00+00:00", speaker=MASTER, text="まだ夜")
    assert sorted(p.name for p in log.directory.glob("*.jsonl")) == ["2026-07-30.jsonl", "2026-07-31.jsonl"]


def test_file_is_utf8_with_lf_line_ends(log: ConversationLog) -> None:
    log.append(ts="2026-07-30T16:00:00+00:00", speaker=MASTER, text="宮古島の海")
    raw = (log.directory / "2026-07-31.jsonl").read_bytes()
    assert b"\r" not in raw
    assert "宮古島の海" in raw.decode("utf-8")


def test_new_lines_have_no_session_and_old_lines_still_read(log: ConversationLog) -> None:
    log.directory.mkdir(parents=True)
    with (log.directory / "2026-07-31.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"ts": "2026-07-30T16:00:00+00:00", "session": "chatgpt-1", "speaker": MASTER, "text": "a"}) + "\n")
        f.write(json.dumps({"ts": "2026-07-30T16:01:00+00:00", "session": "s_20260731_abc", "speaker": MASTER, "text": "b"}) + "\n")
    log.append(ts="2026-07-30T16:02:00+00:00", speaker="Serina", text="c")
    assert _rows(log.directory)[2] == {"ts": "2026-07-30T16:02:00+00:00", "speaker": "Serina", "text": "c"}
    lines = read_conversation(log.directory)
    assert [(line.session, line.text) for line in lines] == [("chatgpt-1", "a"), ("s_20260731_abc", "b"), (None, "c")]
    assert [conversation_source(line.session) for line in lines] == ["first_chat", "local_chat", "local_chat"]


def test_runs_cut_at_old_sessions_but_not_between_new_lines(log: ConversationLog) -> None:
    """眠りの区切り（structure.runs）は、古いセッションの境目では切れて、session のない新しい行の間では切れない。"""
    log.directory.mkdir(parents=True)
    at = datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc)
    with (log.directory / "2026-10-05.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for i, session in enumerate(["s_a", "s_b"]):
            ts = (at + timedelta(minutes=i)).isoformat()
            f.write(json.dumps({"ts": ts, "session": session, "speaker": MASTER, "text": session}) + "\n")
    log.append(ts=(at + timedelta(minutes=2)).isoformat(), speaker=MASTER, text="new1")
    log.append(ts=(at + timedelta(minutes=3)).isoformat(), speaker="Serina", text="new2")
    groups = runs(read_conversation(log.directory), day_of=lambda ts: ts.date())
    assert [[line.text for line in group] for group in groups] == [["s_a"], ["s_b"], ["new1", "new2"]]


def test_add_missing_is_idempotent(log: ConversationLog) -> None:
    """継承した会話を取り込む道具（tools/import_chatgpt_export.py）が使う。"""
    lines = [
        {"ts": "2026-07-30T16:00:00+00:00", "session": "chatgpt-1", "speaker": MASTER, "text": "a"},
        {"ts": "2026-07-30T16:00:01+00:00", "session": "chatgpt-1", "speaker": "Serina", "text": "b"},
    ]
    assert log.add_missing(lines) == 2
    assert log.add_missing(lines) == 0
    assert [line["text"] for line in _lines(log.directory)] == ["a", "b"]


def test_append_tells_where_it_wrote_with_the_reading_line_numbers(log: ConversationLog) -> None:
    said = log.append(ts="2026-07-30T16:00:00+00:00", speaker=MASTER, text="ただいま")
    log_path = next(log.directory.glob("*.jsonl"))
    with log_path.open("a", encoding="utf-8", newline="\n") as f:
        f.write("\n")  # 空行も行番号に数える（読むときと同じ）
    answered = log.append(ts="2026-07-30T16:00:05+00:00", speaker="Serina", text="おかえり")
    lines = read_conversation(log.directory)
    assert [(line.day_file, line.no) for line in lines] == [said, answered]
    assert said[1] == 1 and answered[1] == 3
    assert positions_of(refs_of([said, answered])) == {said, answered}


def test_master_deleting_a_line_marks_it_and_keeps_line_numbers(log: ConversationLog) -> None:
    log.directory.mkdir(parents=True)
    with (log.directory / "2026-07-31.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({"ts": "2026-07-30T16:00:00+00:00", "session": "s_old", "speaker": MASTER, "text": "残す"},
                           ensure_ascii=False) + "\n")
    day, no = log.append(ts="2026-07-30T16:01:00+00:00", speaker=MASTER, text="消す")
    log.append(ts="2026-07-30T16:02:00+00:00", speaker="Serina", text="あとの発言")

    removed = log.remove_at(day, no)

    assert (removed.no, removed.speaker, removed.text) == (2, MASTER, "消す")
    rows = _rows(log.directory)
    assert len(rows) == 3 and rows[1] == {"ts": "2026-07-30T16:01:00+00:00", "speaker": MASTER, "deleted": True}
    assert rows[0]["session"] == "s_old" and rows[0]["text"] == "残す"  # ほかの行は原文のまま
    assert [(line.no, line.text) for line in read_conversation(log.directory)] == [(1, "残す"), (3, "あとの発言")]
    assert log.remove_at(day, no) is None  # もう消してある
    assert log.remove_at(day, 9) is None and log.remove_at("../x", 1) is None
    assert log.remove_at(day, 1).session == "s_old"
    assert _rows(log.directory)[0] == {"ts": "2026-07-30T16:00:00+00:00", "session": "s_old", "speaker": MASTER,
                                       "deleted": True}


def test_a_conversation_line_after_a_half_written_one_gets_its_own_number(log: ConversationLog) -> None:
    log.append(ts="2026-07-30T16:00:00+00:00", speaker=MASTER, text="一つめ")
    path = next(log.directory.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-30T16:01')  # 電源断で書きかけ
    day, no = log.append(ts="2026-07-30T16:02:00+00:00", speaker=MASTER, text="三つめ")
    assert no == 3 and path.read_text(encoding="utf-8").splitlines()[2].endswith('"三つめ"}')


def test_readers_skip_a_half_written_line_and_keep_counting(log: ConversationLog, tmp_path: Path) -> None:
    """書きかけの行があっても、読めて（精神が起動できて）、そのあとの行の番号は変わらず、消すときも原文のまま残る。"""
    log.append(ts="2026-07-30T16:00:00+00:00", speaker=MASTER, text="一つめ")
    path = next(log.directory.glob("*.jsonl"))
    with path.open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-07-30T16:01')  # 電源断で書きかけ
    day, _ = log.append(ts="2026-07-30T16:02:00+00:00", speaker="Serina", text="三つめ")

    assert [(line.no, line.text) for line in read_conversation(log.directory)] == [(1, "一つめ"), (3, "三つめ")]
    assert log.remove_at(day, 2) is None  # 書きかけの行は消す対象にならない
    assert log.remove_at(day, 3).text == "三つめ"
    raws = path.read_text(encoding="utf-8").splitlines()
    assert raws[1] == '{"ts": "2026-07-30T16:01' and json.loads(raws[2])["deleted"] is True

    recall = RecallLog(tmp_path / "recall")
    at = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    recall.append(ts=at, page="ep-a", activation=2.5, vivid=True, intent=False)
    with next((tmp_path / "recall").glob("*.jsonl")).open("a", encoding="utf-8") as f:
        f.write('{"ts": "2026-10-04T12:05')
    recall.append(ts=at + timedelta(minutes=10), page="ep-a", activation=1.0, vivid=False, intent=True)
    assert recall.times() == {"ep-a": [at, at + timedelta(minutes=10)]}


def test_recall_log_keeps_when_each_page_was_remembered(tmp_path: Path) -> None:
    recall = RecallLog(tmp_path / "recall")
    first = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    second = datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc)  # 日本時間では11月
    recall.append(ts=first, page="ep-a", activation=2.5, vivid=True, intent=False)
    recall.append(ts=second, page="ep-a", activation=1.6, vivid=False, intent=True)
    assert recall.times() == {"ep-a": [first, second]}
    assert sorted(p.name for p in (tmp_path / "recall").glob("*.jsonl")) == ["2026-10.jsonl", "2026-11.jsonl"]


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
