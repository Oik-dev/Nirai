"""Coreのセッション境界（end_session）テスト。設計書 §2.4, §2.6。

蒸留の宿題を宿題箱へ小分けで積む挙動と、SessionStateのリセットを検査する。LLM不要（StubBrainのみ）。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.runtime import Core


class StubBrain:
    def __init__(self, script: dict) -> None:
        self.script = script

    def converse(self, pack) -> dict:  # noqa: ANN001
        return self.script


def _thresholds(fragment_turns: int = 20) -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5},
        mood_guard_max_delta_per_turn=0.1,
        chore_fragment_turns=fragment_turns,
    )


def _fresh_chore_box() -> ChoreBox:
    return ChoreBox(Path(tempfile.mkdtemp()) / "test_chore_box.db")


def _reply_brain() -> StubBrain:
    return StubBrain({
        "reply": "うん",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "日常会話"},
    })


def test_end_session_resets_session_state() -> None:
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    core.turn("やあ", _reply_brain())
    assert len(core.session.turns) == 2

    core.end_session()

    assert core.session.turns == []


def test_end_session_without_chore_box_is_noop_safe() -> None:
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    core.turn("やあ", _reply_brain())

    job_ids = core.end_session()

    assert job_ids == []


def test_end_session_enqueues_distillation_job() -> None:
    box = _fresh_chore_box()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)
    core.turn("やあ", _reply_brain())

    job_ids = core.end_session()

    assert len(job_ids) == 1
    jobs = box.pending(kind="蒸留")
    assert len(jobs) == 1
    assert jobs[0].lane == "local"
    turns = jobs[0].payload["turns"]
    # 2026-07-31是正: 発話時刻(ts)を刻むようになったため、speaker/textのみ厳密一致を見て
    # tsは非空であることだけ検査する（記憶の日付帰属を発話時刻に紐付ける前提の回帰確認）。
    assert [{"speaker": t["speaker"], "text": t["text"]} for t in turns] == [
        {"speaker": "master", "text": "やあ"},
        {"speaker": "serina", "text": "うん"},
    ]
    assert all(t["ts"] for t in turns)


def test_end_session_with_empty_session_enqueues_nothing() -> None:
    box = _fresh_chore_box()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(), chore_box=box)

    job_ids = core.end_session()

    assert job_ids == []
    assert box.count() == 0


def test_chore_fragment_flushes_mid_conversation_not_only_at_end_session() -> None:
    """§2.4「会話中: Coreが蒸留の宿題を宿題箱に積む」。器が満ちたら即座に積み、
    end_sessionを待たない（強制終了後も③朝礼で回収できるようにするため）。"""
    box = _fresh_chore_box()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(fragment_turns=2), chore_box=box)
    brain = _reply_brain()

    core.turn("一つ目", brain)  # 1ターン=master+serinaの2行 → fragment_turns=2でちょうど満ちる

    assert box.count(kind="蒸留") == 1  # end_sessionを呼ぶ前に、会話中にもう積まれている

    core.turn("二つ目", brain)

    assert box.count(kind="蒸留") == 2

    job_ids = core.end_session()

    assert job_ids == []  # 端数なし。end_session時点で積むものは残っていない
    assert box.count(kind="蒸留") == 2


def test_end_session_flushes_partial_fragment_remainder() -> None:
    box = _fresh_chore_box()
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(fragment_turns=10), chore_box=box)
    core.turn("やあ", _reply_brain())  # 2行のみ。fragment_turns=10に満たない端数

    assert box.count(kind="蒸留") == 0  # まだ会話中には積まれない

    job_ids = core.end_session()

    assert len(job_ids) == 1  # 端数がend_session時に積まれる
    assert box.count(kind="蒸留") == 1


def main() -> None:
    tests = [
        test_end_session_resets_session_state,
        test_end_session_without_chore_box_is_noop_safe,
        test_end_session_enqueues_distillation_job,
        test_end_session_with_empty_session_enqueues_nothing,
        test_chore_fragment_flushes_mid_conversation_not_only_at_end_session,
        test_end_session_flushes_partial_fragment_remainder,
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
