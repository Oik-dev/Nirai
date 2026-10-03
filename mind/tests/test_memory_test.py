"""Serinaの記憶テスト（mind/memory_test）の仕組みの検査。

守るもの：採点が正しいこと、問題集が勝手に育たないこと（記録はTEST_NOWまで、目印は記録にあるものだけ）、
過去の時点を再現するとき未来の記憶が混ざらないこと、判定の控えが効くこと。
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.memory_test.cases import Case, CaseSet, Turn, load_cases, save_cases
from mind.memory_test.judge import Judge
from mind.memory_test.question_set import TEST_NOW, assemble, replay_cases, time_cases
from mind.memory_test.memory import Recalled
from mind.memory_test.record import JST, Line, Unit, read_units
from mind.memory_test.score import score_case, summarize

NOW = TEST_NOW.isoformat()


def _case(**kw) -> Case:  # noqa: ANN003
    return Case(**({"id": "c", "kind": "direct", "utterance": "覚えてる？", "now": NOW} | kw))


# ---- 採点 ----


def test_marks_hit_when_any_mark_of_every_group_is_recalled() -> None:
    case = _case(kind="change", marks=(("前の様子", "以前"), ("今の様子",)))
    assert score_case(case, [Recalled("ずっと以前のこと"), Recalled("今の 様子は違う")]).hit is True
    assert score_case(case, [Recalled("ずっと以前のこと")]).hit is False


def test_marks_are_also_found_in_the_evidence() -> None:
    """題だけを渡す記憶でも、拠っている記録の原文に目印があれば当たり。"""
    case = _case(marks=(("高野漁港",),))
    assert score_case(case, [Recalled("約束の海の話", evidence="宮古島の**高野漁港**で")]).hit is True


def test_period_counts_events_inside_the_period() -> None:
    case = _case(kind="time", period=("2026-07-31", "2026-07-31"))
    result = score_case(case, [Recalled("a", when=date(2026, 7, 31)), Recalled("b", when=date(2025, 3, 8)), Recalled("c")])
    assert result.hit is True
    assert result.period_share == 1 / 3


def test_silence_passes_only_when_nothing_comes_up() -> None:
    case = _case(kind="silence")
    assert score_case(case, []).hit is True
    assert score_case(case, [Recalled("何か")]).hit is False


def test_summary_reports_noise_for_replays() -> None:
    replay = _case(kind="replay")
    results = [score_case(replay, [Recalled("a"), Recalled("b")], [0, 2]), score_case(replay, [], [])]
    row = summarize(results)["実際の会話"]
    assert row["関係ない想起（1発言あたり）"] == 0.5
    assert row["関係ある想起があった発言"] == 0.5
    assert row["何も浮かばなかった発言"] == 0.5


def test_replay_with_an_answer_keeps_both_the_hit_and_the_judgment() -> None:
    replay = _case(kind="replay", marks=(("高野漁港",),))
    result = score_case(replay, [Recalled("高野漁港のビーチ"), Recalled("関係ない話")], [2, 0])
    assert result.hit is True
    assert result.relevance == (2, 0)
    assert summarize([result])["実際の会話"]["当たり（答えのある1発言）"] == 1.0


# ---- 問題集 ----


def test_cases_round_trip(tmp_path: Path) -> None:
    cases = [
        _case(id="a", marks=(("x", "y"),), recent=(Turn("Master", "前振り"),)),
        _case(id="b", kind="time", period=("2025-03-01", "2025-03-31")),
    ]
    save_cases(tmp_path / "cases.jsonl", cases)
    assert load_cases(tmp_path / "cases.jsonl") == cases


UNIT = Unit(id="diary:2025-03-17", source="diary", day=date(2025, 3, 17), text="宮古島の高野漁港で、約束の海を見た。")


def _idea(tmp_path: Path, lines: list[tuple[datetime, str, str]], questions: list[Case]) -> Path:
    conversation = tmp_path / "lifelog" / "conversation"
    conversation.mkdir(parents=True)
    rows = [{"ts": ts.isoformat(), "session": "s_1", "speaker": speaker, "text": text} for ts, speaker, text in lines]
    (conversation / "2026-07-31.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    save_cases(CaseSet.of_idea(tmp_path).questions, questions)
    return tmp_path


def test_claude_questions_join_the_templates_and_answer_replays(tmp_path: Path) -> None:
    lines = [
        (datetime(2026, 7, 31, 1, 0, tzinfo=JST), "Master", "高野漁港の約束、覚えてる？"),
        (datetime(2026, 7, 31, 1, 1, tzinfo=JST), "Serina", "うん"),
    ]
    answered = _case(id="replay:2026-07-31#1", kind="replay", utterance="高野漁港の約束、覚えてる？", marks=(("高野漁港",),))
    written = _case(id="silence:1", kind="silence", utterance="爪切ってた")
    cases = {case.id: case for case in assemble(_idea(tmp_path, lines, [answered, written]))}
    assert cases["replay:2026-07-31#1"] == answered
    assert cases["silence:1"] == written
    assert "time:2026-07-31:yesterday" in cases


def test_records_after_test_now_do_not_grow_the_question_set(tmp_path: Path) -> None:
    later = TEST_NOW + timedelta(days=1)
    lines = [(later, "Master", "新しい会話"), (later + timedelta(minutes=1), "Serina", "うん")]
    ids = {case.id for case in assemble(_idea(tmp_path, lines, []))}
    assert not any(case_id.startswith(("replay:", "time:")) for case_id in ids)


def test_claude_marks_must_be_in_the_record(tmp_path: Path) -> None:
    lines = [(datetime(2026, 7, 31, 1, 0, tzinfo=JST), "Master", "こんばんは")]
    bad = _case(id="x", marks=(("記録にない言葉",),))
    try:
        assemble(_idea(tmp_path, lines, [bad]))
    except ValueError:
        return
    raise AssertionError("記録にない目印を通してしまった")


def test_time_cases_ask_about_days_with_records() -> None:
    cases = {case.id: case for case in time_cases([UNIT])}
    yesterday = cases["time:2025-03-17:yesterday"]
    assert yesterday.period == ("2025-03-17", "2025-03-17")
    assert yesterday.now_at.date() == date(2025, 3, 18)
    assert cases["time:2025-03:month"].period == ("2025-03-01", "2025-03-31")


def _line(no: int, minute: int, speaker: str, session: str = "s_1") -> Line:
    return Line("2026-07-31", no, datetime(2026, 7, 31, 1, minute, tzinfo=JST), session, speaker, f"発言{no}")


def test_replay_uses_local_master_lines_with_the_flow_before_them() -> None:
    lines = [
        _line(1, 0, "Master", "chatgpt-1"),
        _line(2, 1, "Serina", "s_1"),
        _line(3, 2, "bio", "s_1"),
        _line(4, 3, "Master", "s_1"),
    ]
    cases = replay_cases(lines)
    assert [case.id for case in cases] == ["replay:2026-07-31#4"]
    assert cases[0].recent == (Turn("Serina", "発言2"),)
    assert cases[0].now_at == lines[3].ts


def test_conversation_units_split_at_a_long_pause(tmp_path: Path) -> None:
    conversation = tmp_path / "lifelog" / "conversation"
    conversation.mkdir(parents=True)
    start = datetime(2026, 7, 30, 16, 0, tzinfo=JST)
    rows = [
        {"ts": (start + timedelta(minutes=m)).isoformat(), "session": "s_1", "speaker": s, "text": f"t{m}"}
        for m, s in [(0, "Master"), (1, "Serina"), (2, "Master"), (3, "Serina"), (4, "Master"), (5, "Serina")]
        + [(90, "Master"), (91, "Serina"), (92, "Master"), (93, "Serina"), (94, "Master"), (95, "Serina")]
    ]
    (conversation / "2026-07-30.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    units = read_units(tmp_path / "lifelog")
    assert [unit.id for unit in units] == ["conversation:2026-07-30#1-6", "conversation:2026-07-30#7-12"]
    assert all(unit.source == "local_chat" for unit in units)


def test_diaries_of_the_same_day_in_different_files_get_their_own_ids(tmp_path: Path) -> None:
    diaries = tmp_path / "lifelog" / "legacy" / "記憶"
    diaries.mkdir(parents=True)
    body = "今日はマスターと海の話をした。とても楽しかった一日だった。"
    for name in ("セリナの日記 - 20250317.txt", "セリナの日記 - 20250323.txt"):
        (diaries / name).write_text(f"セリナの日記 - 2025年3月21日\n{body}\n", encoding="utf-8")
    assert [unit.id for unit in read_units(tmp_path / "lifelog")] == ["diary:2025-03-21", "diary:2025-03-21-2"]


# ---- 判定の控え ----


def test_judge_asks_once_and_retries_one_by_one(tmp_path: Path) -> None:
    prompts: list[str] = []

    def ask(prompt: str) -> dict:
        prompts.append(prompt)
        return {"scores": [2]} if prompt.count("【記憶") == 1 else {"scores": [0]}  # まとめて聞くと数が合わない

    case = _case(kind="replay")
    items = [Recalled("一つめ"), Recalled("二つめ")]
    assert Judge(tmp_path / "j.jsonl", ask).relevance(case, items) == [2, 2]
    assert len(prompts) == 3
    assert Judge(tmp_path / "j.jsonl", ask).relevance(case, items) == [2, 2]
    assert len(prompts) == 3  # 二度目は控えから
    assert "一つめ" not in (tmp_path / "j.jsonl").read_text(encoding="utf-8")  # 控えに中身は残さない
