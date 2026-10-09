"""Core全身検査: 台本通りに返す人形(StubBrain)で、会話 → 評価 → 気持ちの記録 → 次のパック、の全フローを検査する。

設計書 §5.2「Core全身検査」。LLM不要・記憶接続なし。

守るもの：
- 返答のあとの評価は、会話を記録したあとに気持ちの記録へ1行残り、次のターンのパック⑤に本人の言葉として戻る。
- 評価を聞けなかったターンも、Masterが来たことは残る（つながりが満ちる）。
- Masterが会話を消したら、その会話に拠った気持ちの言葉は消え、数は残る。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig, load_thresholds
from mind.core.feeling.feelings import Feelings
from mind.core.lifelog import FeelingLog
from mind.core.runtime import Core

NOW = datetime(2026, 10, 6, 5, 0, tzinfo=timezone.utc)  # 日本時間 14時
SOURCE = ["lifelog/conversation/2026-10-06.jsonl#1-2"]


class StubBrain:
    """台本通りに返す人形。設計書 §5.2。"""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.received_pack = None

    def converse(self, pack, **_) -> dict:  # noqa: ANN001
        self.received_pack = pack
        return self.script


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig()


def _report(appraisal: dict | None = None, reply: str = "おかえりなさい") -> dict:
    return {
        "reply": reply,
        "appraisal": appraisal,
    }


def _feeling_core(tmp_path: Path) -> Core:
    feelings = Feelings(FeelingLog(tmp_path / "feeling"), load_thresholds().feeling)
    return Core(persona_text="価値観: 誠実", absolute_rules="機微を漏らさない", thresholds=_thresholds(), feelings=feelings)


HAPPY = {"feeling": "帰ってきてくれて、ほっとした", "valence": "うれしい", "arousal": "少し動いた",
         "distance": "近づいた", "master_state": "疲れてそう"}


def test_turn_then_feel_then_the_next_pack_carries_her_words(tmp_path: Path) -> None:
    core = _feeling_core(tmp_path)
    before = core.feelings.sense(NOW)
    brain = StubBrain(_report(HAPPY))

    result = core.turn("ただいま", brain, now=NOW)
    assert result.report.reply == "おかえりなさい"
    assert [t.text for t in core.session.turns] == ["ただいま", "おかえりなさい"]
    assert result.appraisal is not None and result.appraisal.distance == "近づいた"

    row = core.feel(result, source=SOURCE, now=NOW + timedelta(seconds=5))
    assert row is not None and row["source"] == SOURCE and row["feeling"] == HAPPY["feeling"]
    after = core.feelings.sense(NOW + timedelta(seconds=5))
    assert after.valence > before.valence and after.connection > before.connection
    assert HAPPY["feeling"] not in brain.received_pack.render()  # 評価は返答のあと。そのターンのパックにはまだない


def test_the_next_turn_sees_the_feeling_flow(tmp_path: Path) -> None:
    core = _feeling_core(tmp_path)
    core.feel(core.turn("ただいま", StubBrain(_report(HAPPY)), now=NOW), source=SOURCE, now=NOW)
    brain = StubBrain(_report(HAPPY, reply="うん"))
    core.turn("ねえ", brain, now=NOW + timedelta(minutes=10))
    section = brain.received_pack.render().split("【今のセリナの心の状態】")[1].split("【絶対ルール】")[0]
    assert "10分前：帰ってきてくれて、ほっとした" in section
    assert "マスターの様子（10分前）：疲れてそう" in section
    assert "0." not in section  # 数は載せない


def test_turn_without_appraisal_still_records_that_master_came(tmp_path: Path) -> None:
    core = _feeling_core(tmp_path)
    before = core.feelings.sense(NOW).connection
    row = core.feel(core.turn("やあ", StubBrain(_report(None)), now=NOW), source=SOURCE, now=NOW)
    assert row["evaluation"] is None and row["feeling"] == ""
    assert core.feelings.sense(NOW).connection > before


def test_forget_erases_her_words_but_keeps_the_numbers(tmp_path: Path) -> None:
    core = _feeling_core(tmp_path)
    row = core.feel(core.turn("ただいま", StubBrain(_report(HAPPY)), now=NOW), source=SOURCE, now=NOW)
    assert core.forget([("2026-10-06", 2)]) == []  # 記憶につながっていない（ページはない）
    kept = next(core.feelings.log.rows())
    assert kept["feeling"] == "" and kept["evaluation"]["master_state"] == "" and kept["forgotten"] is True
    assert kept["after"] == row["after"] and kept["evaluation"]["valence"] == "うれしい"
    assert "ほっとした" not in core.feelings.for_pack(NOW)


def test_brain_receives_context_pack_with_master_utterance() -> None:
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain(_report(reply="こんにちは"))
    core.turn("やあ", brain)
    assert brain.received_pack is not None
    assert "やあ" in brain.received_pack.render()
    assert core.feel(core.turn("もう一度", brain), source=SOURCE, now=NOW) is None  # 気持ちがなければ何もしない


def test_second_turn_sees_first_turn_in_recent_history() -> None:
    """Brainはステートレスだが、Coreが毎ターン文脈パックに履歴を積み直す（条文A）"""
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=_thresholds())
    brain = StubBrain(_report(reply="了解です"))
    core.turn("最初の話題", brain)
    core.turn("次の話題", brain)
    assert "最初の話題" in brain.received_pack.render()
    assert "次の話題" in brain.received_pack.render()
