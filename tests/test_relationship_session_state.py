"""関係状態・セッション状態のテスト。設計書v2 §2.6"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.state.relationship import RelationshipState
from serina.core_v2.state.session import SessionState, Turn


def test_relationship_initial_state() -> None:
    rel = RelationshipState()
    assert rel.intimacy == 0.0
    assert rel.recent_master_mood is None
    assert rel.ongoing_topics == []


def test_relationship_observe_updates_fields() -> None:
    rel = RelationshipState()
    rel.observe(master_mood="疲れてそう", topic="仕事の話")
    assert rel.recent_master_mood == "疲れてそう"
    assert "仕事の話" in rel.ongoing_topics


def test_session_records_turns_in_order() -> None:
    session = SessionState()
    session.add_turn(Turn(speaker="master", text="おはよう"))
    session.add_turn(Turn(speaker="serina", text="おはようございます"))
    assert len(session.turns) == 2
    assert session.turns[0].speaker == "master"
    assert session.turns[1].text == "おはようございます"


def test_session_rolling_summary_default_empty() -> None:
    session = SessionState()
    assert session.rolling_summary == ""
    session.rolling_summary = "朝の挨拶を交わした"
    assert session.rolling_summary == "朝の挨拶を交わした"


def main() -> None:
    tests = [
        test_relationship_initial_state,
        test_relationship_observe_updates_fields,
        test_session_records_turns_in_order,
        test_session_rolling_summary_default_empty,
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
