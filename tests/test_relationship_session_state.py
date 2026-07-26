"""関係状態・セッション状態のテスト。設計書 §2.6"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import ThresholdsConfig
from serina.core.context.relationship_render import render_master_observation_for_pack
from serina.core.state.relationship import MAX_ONGOING_TOPICS, RelationshipState
from serina.core.state.relationship_persist import (
    apply_loaded_to_relationship,
    load_relationship_state,
    save_relationship_from_state,
)
from serina.core.state.session import SessionState, Turn


def test_relationship_initial_state() -> None:
    rel = RelationshipState()
    assert rel.intimacy == 0.0
    assert rel.recent_master_mood is None
    assert rel.recent_master_mood_at is None
    assert rel.ongoing_topics == []


def test_relationship_observe_updates_fields() -> None:
    rel = RelationshipState()
    rel.observe(master_mood="疲れてそう", topic="仕事の話")
    assert rel.recent_master_mood == "疲れてそう"
    assert "仕事の話" in rel.ongoing_topics


def test_relationship_observe_stamps_timestamp() -> None:
    """2026-07-26 B1: 観測に取得時刻（時刻の錨）が付く。"""
    rel = RelationshipState()
    assert rel.recent_master_mood_at is None
    rel.observe(master_mood="嬉しそう")
    assert rel.recent_master_mood_at is not None


def test_relationship_observe_uses_current_turn_at_when_set() -> None:
    """current_turn_at（runtime.pyが毎ターン設定）が優先される。"""
    rel = RelationshipState()
    stamped = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
    rel.current_turn_at = stamped
    rel.observe(master_mood="眠そう")
    assert rel.recent_master_mood_at == stamped


def test_relationship_ongoing_topics_capped() -> None:
    """2026-07-26 B1: ongoing_topicsは追記のみで際限なく膨張しないよう上限を持つ。"""
    rel = RelationshipState()
    for i in range(MAX_ONGOING_TOPICS + 5):
        rel.observe(topic=f"話題{i}")
    assert len(rel.ongoing_topics) == MAX_ONGOING_TOPICS
    # 直近のものだけが残る（古いものから捨てる）
    assert rel.ongoing_topics[-1] == f"話題{MAX_ONGOING_TOPICS + 4}"
    assert "話題0" not in rel.ongoing_topics


def _thresholds(stale_after: float = 21600.0) -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        relationship_observation_stale_after_seconds=stale_after,
    )


def test_render_master_observation_empty_when_never_observed() -> None:
    rel = RelationshipState()
    assert render_master_observation_for_pack(rel, _thresholds()) == ""


def test_render_master_observation_none_relationship_is_empty() -> None:
    assert render_master_observation_for_pack(None, _thresholds()) == ""


def test_render_master_observation_shows_fresh_observation() -> None:
    rel = RelationshipState()
    now = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)
    rel.current_turn_at = now
    rel.observe(master_mood="機嫌が良さそう")
    text = render_master_observation_for_pack(rel, _thresholds(), now=now + timedelta(minutes=5))
    assert text == "機嫌が良さそう"


def test_render_master_observation_omits_stale_observation() -> None:
    """2026-07-26 B1: architecture-reviewer指摘の再発防止。3日前の観測を『今の様子』
    として提示しない（鮮度切れは行ごと省略）。"""
    rel = RelationshipState()
    observed_at = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
    rel.current_turn_at = observed_at
    rel.observe(master_mood="疲れてそう")
    three_days_later = observed_at + timedelta(days=3)
    text = render_master_observation_for_pack(rel, _thresholds(), now=three_days_later)
    assert text == ""


def test_render_master_observation_stale_threshold_is_configurable() -> None:
    rel = RelationshipState()
    observed_at = datetime(2026, 7, 26, 6, 0, tzinfo=timezone.utc)
    rel.current_turn_at = observed_at
    rel.observe(master_mood="眠そう")
    just_after = observed_at + timedelta(hours=1)
    # stale_after=30分なら1時間後はもう鮮度切れ
    assert render_master_observation_for_pack(rel, _thresholds(stale_after=1800), now=just_after) == ""
    # stale_after=2時間ならまだ表示される
    assert render_master_observation_for_pack(rel, _thresholds(stale_after=7200), now=just_after) == "眠そう"


def test_relationship_persist_round_trip() -> None:
    path = Path(tempfile.mkdtemp()) / "relationship_state.json"
    rel = RelationshipState()
    rel.current_turn_at = datetime(2026, 7, 26, 9, 0, tzinfo=timezone.utc)
    rel.observe(master_mood="穏やかそう", topic="旅行の計画")
    rel.intimacy = 0.3

    save_relationship_from_state(path, rel)

    restored = RelationshipState()
    apply_loaded_to_relationship(restored, load_relationship_state(path))
    assert restored.recent_master_mood == "穏やかそう"
    assert restored.recent_master_mood_at == rel.recent_master_mood_at
    assert restored.ongoing_topics == ["旅行の計画"]
    assert restored.intimacy == 0.3


def test_relationship_persist_missing_file_returns_defaults() -> None:
    path = Path(tempfile.mkdtemp()) / "missing.json"
    data = load_relationship_state(path)
    restored = RelationshipState()
    apply_loaded_to_relationship(restored, data)
    assert restored.recent_master_mood is None
    assert restored.recent_master_mood_at is None
    assert restored.ongoing_topics == []
    assert restored.intimacy == 0.0


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


def test_relationship_persist_naive_timestamp_is_treated_as_utc() -> None:
    """2026-07-26 B1是正(serina-code-reviewer指摘I-4): JSONを手編集してtzオフセット
    無しの時刻を書いても、UTCとして補完される（naive×aware減算でTypeErrorにしない）。"""
    path = Path(tempfile.mkdtemp()) / "relationship_state.json"
    path.write_text(
        '{"recent_master_mood": "眠そう", "recent_master_mood_at": "2026-07-26T09:00:00", '
        '"ongoing_topics": [], "intimacy": 0.0}',
        encoding="utf-8",
    )
    restored = RelationshipState()
    apply_loaded_to_relationship(restored, load_relationship_state(path))
    assert restored.recent_master_mood_at is not None
    assert restored.recent_master_mood_at.tzinfo is not None

    # render側でも例外を出さない
    text = render_master_observation_for_pack(
        restored, _thresholds(), now=datetime(2026, 7, 26, 9, 30, tzinfo=timezone.utc),
    )
    assert text == "眠そう"


def test_render_master_observation_never_raises_on_naive_datetime() -> None:
    """apply_loaded_to_relationshipを経由せず直接naiveを設定した場合の最終防衛線。"""
    rel = RelationshipState()
    rel.recent_master_mood = "困っていそう"
    rel.recent_master_mood_at = datetime(2026, 7, 26, 9, 0)  # tzinfo無し(naive)
    text = render_master_observation_for_pack(
        rel, _thresholds(), now=datetime(2026, 7, 26, 9, 30, tzinfo=timezone.utc),
    )
    assert text == ""  # 例外を出さず安全側（未観測扱い）に倒す


def main() -> None:
    tests = [
        test_relationship_initial_state,
        test_relationship_observe_updates_fields,
        test_session_records_turns_in_order,
        test_turn_tracks_which_location_handled_it,
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
