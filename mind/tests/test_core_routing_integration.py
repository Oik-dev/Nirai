"""Core.turn_routed の配線テスト。設計書 §3.2(決定論チェックリスト), §3.5(フォールバック)

家の印のない Core（印を持たない＝すべて家）なので、話すのはいつも手元の脳だけ。
手元の脳が失敗したときの最終防衛線（§3.2）をここで確かめる。クラウドの順送りは test_gemini_cascade.py。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig, load_thresholds
from mind.core.perception import BodyCatalog, BodyChoice
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _registry() -> list[BrainEntry]:
    """手元の脳1つ（家の話はクラウドへ出ないので、この Core の候補はこれだけ）。"""
    return [BrainEntry("gemma", "ollama", "local", "small")]


def _thresholds() -> ThresholdsConfig:
    return load_thresholds()


class ScriptedBrain:
    def __init__(
        self, script: dict | None = None, raise_error: bool = False, raise_cls: type[Exception] = RuntimeError,
    ) -> None:
        self.script = script
        self.raise_error = raise_error
        self.raise_cls = raise_cls
        self.call_count = 0

    def converse(self, pack, **_) -> dict:  # noqa: ANN001
        self.call_count += 1
        if self.raise_error:
            raise self.raise_cls("Brain呼び出しが失敗した")
        return self.script


def _report() -> dict:
    return {"reply": "了解です"}


def _core(brains: dict, memory=None, registry: list[BrainEntry] | None = None) -> Core:  # noqa: ANN001
    return Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=registry or _registry(), brains=brains,
        memory=memory,
    )


class _BrokenMemory:
    """思い出そうとすると、埋め込みの瞬断で失敗する記憶。"""

    def recall(self, cue, *, already=()):  # noqa: ANN001, ANN201
        raise RuntimeError("Ollamaが瞬断した")


def test_normal_turn_uses_the_local_brain() -> None:
    gemma = ScriptedBrain(_report())
    core = _core({"gemma": gemma})

    core.turn_routed("こんにちは", now=NOW)

    assert gemma.call_count == 1


@pytest.mark.parametrize("brain", [
    ScriptedBrain(raise_error=True),
    ScriptedBrain(raise_error=True, raise_cls=ConnectionError),
    ScriptedBrain({"reply": ""}),
])
def test_never_crashes_when_the_local_brain_fails(brain: ScriptedBrain) -> None:
    """§3.2最終防衛線: 手元の脳が例外でも書式違反でも、機械的な既定応答で沈黙しない（家の話はクラウドへ逃げない）。"""
    core = _core({"gemma": brain})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"
    assert brain.call_count == 1


class _StreamingBrain:
    """callbacks対応・judge計測つきのフェイクBrain（2026-07-20 応答高速化の配線検査用）。"""

    def __init__(self, script: dict) -> None:
        self.script = script
        self.judge_calls = 0
        self.received_think: list[bool] = []
        self.received_packs: list = []
        self.on_reply_fired: list[str] = []

    def judge(self, prompt: str) -> dict:  # noqa: ANN001
        self.judge_calls += 1
        return {"needs_deep_thinking": True, "reason": "judge発注された"}

    def converse(self, pack, *, think=False, on_token=None, on_reply=None, **_) -> dict:  # noqa: ANN001
        self.received_think.append(think)
        self.received_packs.append(pack)
        reply = self.script.get("reply", "")
        if on_token is not None:
            for ch in reply:
                on_token(ch)
        if reply and on_reply is not None:
            on_reply(reply)
            self.on_reply_fired.append(reply)
        return dict(self.script)

def test_a_brain_cannot_ask_for_a_search_in_its_report() -> None:
    """外聞きの入口は Core の判定だけ（§3.7）。脳が報告書に検索の頼みを書いても、何も起きない。"""
    brain = _StreamingBrain(
        dict(_report(), search=[{"type": "web_search", "query": "宮古島の方言の意味"}]),
    )
    core = _core({"gemma": brain})

    result = core.turn_routed("宮古島の方言ってどういう意味？ちょっと教えて", now=NOW)

    assert result.report.reply == "了解です"
    texts = [t.text for t in core.session.turns]
    assert texts == [
        "宮古島の方言ってどういう意味？ちょっと教えて",
        "了解です",
    ]


def test_streaming_callbacks_reach_brain_and_fire_in_order() -> None:
    tokens: list[str] = []
    fired: list[str] = []
    brain = _StreamingBrain(_report())
    core = _core({"gemma": brain})

    core.turn_routed("こんにちは", now=NOW, on_token=tokens.append, on_reply=fired.append)

    assert "".join(tokens) == "了解です"
    assert fired == ["了解です"]


def test_think_rules_skip_judge_for_casual_and_explicit_utterances() -> None:
    """ルール先行（2026-07-20）: 雑談は即false・明示深考は即trueで、think判定のjudge（LLM往復）を
    呼ばない。中間帯マーカーのみ judge へ委任する。

    2026-07-31改訂: Tavily検索要否判定（Phase B）は合言葉ゼロのため、Gemini呼びかけが無い
    発話では（本テストの発話はいずれも該当）毎回judgeが1回走る（think判定とは別目的の判定・
    同じbrain.judgeを共有するため呼び出し回数に乗る）。本テストの主眼はthink判定が追加で
    judgeを呼ぶかどうかであり、Tavily分の呼び出しをターンごとにリセットして切り分ける。
    """
    brain = _StreamingBrain(_report())
    core = _core({"gemma": brain})

    core.turn_routed("おはよう", now=NOW)
    assert brain.judge_calls == 0, "旧Core経路には家の印が無いので検索判定は行わない"
    assert brain.received_think[-1] is False

    brain.judge_calls = 0
    core.turn_routed("この命題を証明してほしい", now=NOW)
    assert brain.judge_calls == 0, "明示の深考要求はルール即決"
    assert brain.received_think[-1] is True

    brain.judge_calls = 0
    core.turn_routed("これってどう思う？", now=NOW)
    assert brain.judge_calls >= 1, "中間帯はthink判定でjudgeへ委任すべき"
    assert brain.received_think[-1] is True, "judgeがtrueと答えたらthink ON"


def test_memory_failure_does_not_break_conversation() -> None:
    """思い出すのに失敗しても（埋め込みの瞬断など）、何も思い出さずに返答は返るべき。"""
    primary = ScriptedBrain(_report())
    core = _core({"gemma": primary}, memory=_BrokenMemory())

    result = core.turn_routed("散歩が好きなんだ", now=NOW)

    assert result.report.reply, "記憶が壊れていても会話の返答は届くべき"
    assert primary.call_count == 1


def test_the_brain_is_asked_with_the_catalog_known_at_the_start_and_only_valid_choices_come_back() -> None:
    """体のカタログは、ターンの初めに覚えていたものを脳に見せ、その同じカタログで答えを確かめてから渡す。"""
    seen: list = []
    catalog = BodyCatalog(expressions=("喜び",), gestures=("うなずく",))

    class BodyBrain:
        def converse(self, pack, *, catalog=None, on_body=None, **_):  # noqa: ANN001
            seen.append(catalog)
            if on_body is not None:
                core.perceive(BodyCatalog(expressions=("跳ねる",)))  # 答えの途中で体が変わっても、聞いたカタログで確かめる
                on_body({"expression": "喜び", "gesture": "跳ねる"})
                on_body({"expression": "そのまま"})
            return _report()

    core = _core({"gemma": BodyBrain()})
    core.perceive(catalog)
    chosen: list = []

    core.turn_routed("こんにちは", now=NOW, on_body=chosen.append)
    core.turn_routed("こんにちは", now=NOW)  # 体を受け取る先がなければ、体は聞かない

    assert seen == [catalog, None]
    assert chosen == [BodyChoice(expression="喜び")]


def test_the_reply_core_gives_itself_is_announced_like_a_brain_reply() -> None:
    """脳が返答を出せず既定の返答になっても、記録に残す返答として1回知らせる。"""
    fired: list[str] = []
    core = _core({"gemma": ScriptedBrain(raise_error=True)})

    result = core.turn_routed("こんにちは", now=NOW, on_reply=fired.append)

    assert fired == [result.report.reply]
