"""手元の会話の流れ（core/state/session.py）のテスト。LLM不要。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.state.session import SessionState, Turn


def test_session_records_turns_in_order() -> None:
    session = SessionState()
    session.add_turn(Turn(speaker="master", text="おはよう"))
    session.add_turn(Turn(speaker="serina", text="おはようございます"))
    assert len(session.turns) == 2
    assert session.turns[0].speaker == "master"
    assert session.turns[1].text == "おはようございます"


def test_turn_tracks_which_location_handled_it() -> None:
    """§3.3第3経路: どのターンがローカル担当だったかを記録する（プレースホルダ置換の前提）"""
    turn = Turn(speaker="serina", text="内緒の話", location="local")
    assert turn.location == "local"
    default_turn = Turn(speaker="master", text="やあ")
    assert default_turn.location is None


def test_session_rolling_summary_default_empty() -> None:
    session = SessionState()
    assert session.rolling_summary == ""
    session.rolling_summary = "朝の挨拶を交わした"
    assert session.rolling_summary == "朝の挨拶を交わした"
