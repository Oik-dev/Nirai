"""転がし要約（rolling_summary / fine_summary）のテスト。設計書 §1.4, §1.5。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.rolling_summary import (
    build_coarse_summary_prompt,
    build_fine_summary_prompt,
    coarse_overflow_turns,
    fine_band_turns,
    overflow_turns,
    update_coarse_rolling_summary,
    update_fine_summary,
    update_rolling_summary,
    update_turn_summaries,
)
from mind.core.config import ThresholdsConfig
from mind.core.state.session import SessionState, Turn


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        fine_band_turns=4,
        coarse_update_every_n_turns=3,
    )


def _fill(session: SessionState, n: int) -> None:
    for i in range(n):
        session.add_turn(Turn(speaker="master", text=f"発言{i}"))


def test_summary_prompts_use_japanese_speaker_labels() -> None:
    turns = [Turn(speaker="master", text="今日は疲れた"), Turn(speaker="serina", text="お疲れさま")]
    fine_prompt = build_fine_summary_prompt(turns=turns)
    coarse_prompt = build_coarse_summary_prompt(existing_summary="", turns=turns)
    assert "マスター: 今日は疲れた" in fine_prompt
    assert "セリナ: お疲れさま" in fine_prompt
    assert "マスター: 今日は疲れた" in coarse_prompt
    assert "master:" not in fine_prompt
    assert "serina:" not in coarse_prompt


def test_fine_band_turns_returns_last_n() -> None:
    session = SessionState()
    _fill(session, 8)
    band = fine_band_turns(session, band_size=4)
    assert [t.text for t in band] == [f"発言{i}" for i in range(4, 8)]


def test_coarse_overflow_excludes_fine_band_and_respects_cursor() -> None:
    session = SessionState()
    _fill(session, 10)
    session.summarized_turn_count = 2
    overflow = coarse_overflow_turns(session, fine_band_turns=4)
    assert [t.text for t in overflow] == [f"発言{i}" for i in range(2, 6)]


def test_overflow_turns_are_those_outside_window_and_not_yet_summarized() -> None:
    session = SessionState()
    _fill(session, 10)
    overflow = overflow_turns(session, window_size=4)
    assert [t.text for t in overflow] == [f"発言{i}" for i in range(6)]


def test_update_fine_summary_stores_text_and_keeps_cursor_on_failure() -> None:
    session = SessionState()
    _fill(session, 5)

    ok = update_fine_summary(session, call_fn=lambda p: "直近は天気の話", band_size=4)
    assert ok.updated is True
    assert session.fine_summary == "直近は天気の話"

    session.fine_summary = "据え置き"
    fail = update_fine_summary(
        session,
        call_fn=lambda p: (_ for _ in ()).throw(RuntimeError("通信エラー")),
        band_size=4,
    )
    assert fail.updated is False
    assert session.fine_summary == "据え置き"


def test_update_coarse_advances_cursor_one_step_batch() -> None:
    session = SessionState()
    _fill(session, 10)

    outcome = update_coarse_rolling_summary(
        session,
        call_fn=lambda p: "序盤の流れ",
        fine_band_turns=4,
        step_turns=3,
    )
    assert outcome.updated is True
    assert session.rolling_summary == "序盤の流れ"
    assert session.summarized_turn_count == 3
    assert len(coarse_overflow_turns(session, fine_band_turns=4)) == 3


def test_update_coarse_noop_until_step_reached() -> None:
    session = SessionState()
    _fill(session, 6)
    outcome = update_coarse_rolling_summary(
        session,
        call_fn=lambda p: "呼ばれない",
        fine_band_turns=4,
        step_turns=3,
    )
    assert outcome.updated is False
    assert session.summarized_turn_count == 0


def test_update_coarse_keeps_cursor_on_llm_failure() -> None:
    session = SessionState()
    _fill(session, 10)

    def failing(prompt: str) -> str:
        raise RuntimeError("通信エラー")

    outcome = update_coarse_rolling_summary(
        session, call_fn=failing, fine_band_turns=4, step_turns=3,
    )
    assert outcome.updated is False
    assert session.summarized_turn_count == 0
    assert session.rolling_summary == ""


def test_update_turn_summaries_runs_fine_and_conditional_coarse() -> None:
    session = SessionState()
    _fill(session, 10)
    prompts: list[str] = []

    def call_fn(prompt: str) -> str:
        prompts.append(prompt)
        if "直近の会話" in prompt:
            return "細かめ直近"
        return "粗い追記"

    batch = update_turn_summaries(session, call_fn=call_fn, thresholds=_thresholds())
    assert batch.fine.updated is True
    assert batch.coarse.updated is True
    assert session.fine_summary == "細かめ直近"
    assert session.rolling_summary == "粗い追記"
    assert session.summarized_turn_count == 3
    assert len(prompts) == 2


def test_update_rolling_summary_advances_cursor_and_stores_text() -> None:
    session = SessionState()
    _fill(session, 6)

    def call_fn(prompt: str) -> str:
        assert "発言0" in prompt
        assert "発言1" in prompt
        return "序盤は自己紹介の話をした"

    outcome = update_rolling_summary(session, call_fn=call_fn, window_size=4)
    assert outcome.updated is True
    assert session.rolling_summary == "序盤は自己紹介の話をした"
    assert session.summarized_turn_count == 2
    assert overflow_turns(session, window_size=4) == []


def test_update_rolling_summary_noop_when_nothing_overflows() -> None:
    session = SessionState()
    _fill(session, 3)
    outcome = update_rolling_summary(
        session, call_fn=lambda p: "呼ばれないはず", window_size=4,
    )
    assert outcome.updated is False
    assert session.summarized_turn_count == 0


def test_update_rolling_summary_keeps_cursor_on_llm_failure() -> None:
    session = SessionState()
    _fill(session, 6)

    def failing(prompt: str) -> str:
        raise RuntimeError("通信エラー")

    outcome = update_rolling_summary(session, call_fn=failing, window_size=4)
    assert outcome.updated is False
    assert session.summarized_turn_count == 0
    assert session.rolling_summary == ""


def main() -> None:
    tests = [
        test_summary_prompts_use_japanese_speaker_labels,
        test_fine_band_turns_returns_last_n,
        test_coarse_overflow_excludes_fine_band_and_respects_cursor,
        test_overflow_turns_are_those_outside_window_and_not_yet_summarized,
        test_update_fine_summary_stores_text_and_keeps_cursor_on_failure,
        test_update_coarse_advances_cursor_one_step_batch,
        test_update_coarse_noop_until_step_reached,
        test_update_coarse_keeps_cursor_on_llm_failure,
        test_update_turn_summaries_runs_fine_and_conditional_coarse,
        test_update_rolling_summary_advances_cursor_and_stores_text,
        test_update_rolling_summary_noop_when_nothing_overflows,
        test_update_rolling_summary_keeps_cursor_on_llm_failure,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
