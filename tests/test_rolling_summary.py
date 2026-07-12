"""転がし要約（rolling_summary）のテスト。設計書v2 §1.4。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.chores.rolling_summary import (
    overflow_turns,
    update_rolling_summary,
)
from serina.core_v2.state.session import SessionState, Turn


def _fill(session: SessionState, n: int) -> None:
    for i in range(n):
        session.add_turn(Turn(speaker="master", text=f"発言{i}"))


def test_overflow_turns_are_those_outside_window_and_not_yet_summarized() -> None:
    session = SessionState()
    _fill(session, 10)
    overflow = overflow_turns(session, window_size=4)
    assert [t.text for t in overflow] == [f"発言{i}" for i in range(6)]


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
        test_overflow_turns_are_those_outside_window_and_not_yet_summarized,
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
