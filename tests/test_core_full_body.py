"""Core全身検査: 台本通りに返す人形(StubBrain)で会話→付箋→状態更新の全フローを検査する。

設計書 §5.2「Core全身検査」。LLM不要・記憶接続なし（Phase2で接続）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import ThresholdsConfig
from serina.core.runtime import Core


class StubBrain:
    """台本通りに返す人形。設計書 §5.2。"""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.received_pack = None

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.received_pack = pack
        return self.script


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
    )


def test_turn_updates_state_and_records_session() -> None:
    core = Core(persona_text="価値観: 誠実", absolute_rules="機微を漏らさない", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "おかえりなさい",
        "fusen_list": [
            {
                "kind": "心の動き",
                "version": 1,
                "content": {"deltas": {"喜び": 0.4}, "trigger": "帰宅の挨拶"},
                "confidence": 0.9,
            }
        ],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    result = core.turn("ただいま", brain)

    assert result.report.reply == "おかえりなさい"
    assert core.emotion.affect["喜び"] == 0.4
    assert len(core.session.turns) == 2
    assert core.session.turns[0].text == "ただいま"
    assert core.session.turns[1].text == "おかえりなさい"
    # 2026-07-26 Minor是正: 非routed経路でも軌跡タグ・観測時刻を付与する
    assert core.emotion.current_day is not None
    assert core.relationship.current_turn_at is not None
    assert any(
        snap.get("_day") == core.emotion.current_day
        for snap in core.emotion.mood_trajectory
    )


def test_turn_uses_configured_boundary_hour() -> None:
    """boundary_hour≠7でも軌跡の_dayが設定値基準になる。"""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from serina.core.state.serina_day import serina_day_id

    jst = ZoneInfo("Asia/Tokyo")
    # 05:30 JST: 既定7なら前日、boundary=5なら当日
    now = datetime(2026, 7, 26, 5, 30, tzinfo=jst).astimezone(timezone.utc)
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=_thresholds(),
        serina_day_boundary_hour=5,
    )
    brain = StubBrain({
        "reply": "了解",
        "fusen_list": [{
            "kind": "心の動き",
            "version": 1,
            "content": {"deltas": {"喜び": 0.2}, "trigger": "t"},
            "confidence": 0.9,
        }],
        "self_assessment": {"over_capacity": False, "reason": "x"},
    })
    core.turn("やあ", brain, now=now)
    expected = serina_day_id(now, boundary_hour=5).isoformat()
    assert core.emotion.current_day == expected
    assert expected != serina_day_id(now, boundary_hour=7).isoformat()


def test_brain_receives_context_pack_with_master_utterance() -> None:
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "こんにちは",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("やあ", brain)

    assert brain.received_pack is not None
    assert "やあ" in brain.received_pack.render()


def test_second_turn_sees_first_turn_in_recent_history() -> None:
    """Brainはステートレスだが、Coreが毎ターン文脈パックに履歴を積み直す（条文A）"""
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain({
        "reply": "了解です",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })

    core.turn("最初の話題", brain)
    core.turn("次の話題", brain)

    assert "最初の話題" in brain.received_pack.render()
    assert "次の話題" in brain.received_pack.render()


def _fresh_memory_store():
    import tempfile

    from serina.core.memory.embedder import OllamaEmbedder
    from serina.core.memory.store import MemoryStore, RecallParams

    embedder = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    db_path = Path(tempfile.mkdtemp()) / "full_body_facts.db"
    return MemoryStore(
        str(db_path),
        embedder=embedder,
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def test_schedule_propose_fact_writes_active_immediately(tmp_path: Path) -> None:
    """(a) 日時抽出成功＋propose_fact一致で即時 active に書かれる。"""
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE
    from serina.core.memory.protection import ChangeLog

    store = _fresh_memory_store()
    change_log = ChangeLog(tmp_path / "changes.jsonl")
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=_thresholds(),
        memory_store=store,
        change_log=change_log,
    )
    brain = StubBrain({
        "reply": "わかった、病院だね",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "予定"},
        "memory_tool_calls": [{
            "type": "propose_fact",
            "statement": "7月28日の15時に病院",
            "category": FACT_CATEGORY_SCHEDULE,
        }],
    })
    result = core.turn("7月28日の15時に病院があるよ", brain)

    facts = store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE)
    assert len(facts) == 1
    assert facts[0].status == "active"
    assert facts[0].episode_ids == []
    assert "病院" in facts[0].statement
    # 想起記憶（memories）には格納しない
    assert store.list_by_type("semantic", limit=10) == []
    assert store.list_by_type("episodic", limit=10) == []
    assert any(
        p.get("status") == "accepted_immediate"
        for p in (result.memory_tool_outcome.proposals if result.memory_tool_outcome else [])
    )


def test_schedule_propose_fact_defers_when_extract_fails(tmp_path: Path) -> None:
    """(b) 抽出失敗で通常蒸留経路に委ねられる（即時書き込みしない）。"""
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE
    from serina.core.memory.protection import ChangeLog

    store = _fresh_memory_store()
    change_log = ChangeLog(tmp_path / "changes.jsonl")
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=_thresholds(),
        memory_store=store,
        change_log=change_log,
    )
    brain = StubBrain({
        "reply": "うん",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "x"},
        "memory_tool_calls": [{
            "type": "propose_fact",
            "statement": "いつか病院に行きたい",
            "category": FACT_CATEGORY_SCHEDULE,
        }],
    })
    result = core.turn("なんか予定あるかも", brain)

    assert store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE) == []
    assert result.memory_tool_outcome is not None
    assert result.memory_tool_outcome.proposals[0]["status"] == "pending_distillation"
    actions = [r.action for r in change_log.read_all()]
    assert "予定即時書き込み見送り" in actions


def test_schedule_propose_fact_suppresses_duplicate(tmp_path: Path) -> None:
    """(c) 同一日時・同一カテゴリの重複が抑止され変更レポートに記録される。"""
    from serina.core.memory.facts import FACT_CATEGORY_SCHEDULE
    from serina.core.memory.protection import ChangeLog

    store = _fresh_memory_store()
    change_log = ChangeLog(tmp_path / "changes.jsonl")
    # 既存 fact（同一 valid_from）
    store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="既存の病院予定",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-28T15:00:00+09:00",
    )
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=_thresholds(),
        memory_store=store,
        change_log=change_log,
    )
    brain = StubBrain({
        "reply": "もう登録してあるよ",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "x"},
        "memory_tool_calls": [{
            "type": "propose_fact",
            "statement": "7月28日15時に病院",
            "category": FACT_CATEGORY_SCHEDULE,
        }],
    })
    result = core.turn("7月28日の15時に病院だよ", brain)

    assert len(store.facts.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE)) == 1
    assert result.memory_tool_outcome is not None
    assert result.memory_tool_outcome.proposals[0]["status"] == "suppressed_duplicate"
    actions = [r.action for r in change_log.read_all()]
    assert "予定即時書き込み抑止（重複）" in actions


def main() -> None:
    tests = [
        test_turn_updates_state_and_records_session,
        test_turn_uses_configured_boundary_hour,
        test_brain_receives_context_pack_with_master_utterance,
        test_second_turn_sees_first_turn_in_recent_history,
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
