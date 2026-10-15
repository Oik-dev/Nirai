"""Core.turn_routed の配線テスト。設計書 §3.2(決定論チェックリスト), §3.5(フォールバック)

耐障害（§3.2最終防衛線・§3.5フォールバック作法）は primary+fallback の2Brain登録簿で確かめ、
実運用のBrain単一構成（fallback役が登録簿にない）での収束は _single_registry 系のテストで確かめる。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import ThresholdsConfig
from mind.core.perception import BodyCatalog, BodyChoice
from mind.core.routing.registry import BrainEntry
from mind.core.runtime import Core

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _registry() -> list[BrainEntry]:
    """primary と fallback の2Brain登録簿（§3.5 の代打を確かめる）。"""
    return [
        BrainEntry("primary_brain", "ollama", "local", "primary", "small"),
        BrainEntry("fallback_brain", "ollama", "local", "fallback", "small"),
    ]


def _single_registry() -> list[BrainEntry]:
    """実運用（Brain単一構成・config/brains.toml）と同型。fallback役が登録簿に存在しない。"""
    return [BrainEntry("primary_brain", "ollama", "local", "primary", "small")]


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig()


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


def test_normal_turn_uses_primary_brain() -> None:
    primary = ScriptedBrain(_report())
    fallback = ScriptedBrain(_report())
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    core.turn_routed("こんにちは", now=NOW)

    assert primary.call_count == 1
    assert fallback.call_count == 0


def test_single_brain_registry_never_crashes_when_primary_fails() -> None:
    """§9.1: fallback役が登録簿に存在しない実運用構成（Brain単一）でも、
    Brainが全滅すれば機械的な既定応答に安全側収束する（セリナは沈黙しない）。
    """
    primary = ScriptedBrain(raise_error=True)
    core = _core({"primary_brain": primary}, registry=_single_registry())

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "唯一のBrainが全滅しても何らかの返答が返るべき"


def test_local_communication_error_never_tries_another_brain() -> None:
    """家の印があるターンでは、別の脳を呼ばずに定型の最小返事へ閉じる。"""
    primary = ScriptedBrain(raise_error=True, raise_cls=ConnectionError)
    fallback = ScriptedBrain(_report())
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("ただの雑談のつもりの発言", now=NOW)

    assert fallback.call_count == 0
    assert result.report.reply


def test_local_contract_violation_never_leaves_home() -> None:
    """家の印では書式違反でもクラウドへ逃げない。"""
    malformed = ScriptedBrain({"reply": ""})
    fallback = ScriptedBrain(_report())
    core = _core({"primary_brain": malformed, "fallback_brain": fallback})

    result = core.turn_routed("書式が崩れるだけの発言", now=NOW)

    assert fallback.call_count == 0
    assert result.report.reply


def test_never_crashes_when_fallback_extraction_always_fails() -> None:
    """§3.2最終防衛線: primaryが拒否→fallback代打も全滅(adapter raise)しても沈黙しない"""
    class RaisingBrain:
        def converse(self, pack, **_):  # noqa: ANN001
            raise RuntimeError("完全に応答不能")

    primary = ScriptedBrain(raise_error=True)
    fallback = RaisingBrain()
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


def test_never_crashes_when_fallback_returns_malformed_report() -> None:
    """§3.2最終防衛線: fallbackが書式違反の報告書を返しても沈黙しない"""
    class MalformedBrain:
        def converse(self, pack, **_):  # noqa: ANN001
            return {"reply": ""}

    primary = ScriptedBrain(raise_error=True)
    fallback = MalformedBrain()
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


class _StreamingBrain:
    """callbacks対応・judge計測つきのフェイクBrain（2026-07-20 応答高速化の配線検査用）。"""

    def __init__(self, script: dict, followup: str = "") -> None:
        self.script = script
        self.followup = followup
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

    def compose_advisor_followup(self, pack, stage1_reply, advisor_results, *, think=False) -> str:  # noqa: ANN001
        return self.followup


def test_normal_chat_ignores_advisor_tool_calls_from_converse() -> None:
    """通常会話は外聞きしない（外聞きの入口は Core の判定だけ。§3.7）。converse が advisor_tool_calls を
    返しても followup は生えない。外聞きは事実レーンのみ。
    """
    brain = _StreamingBrain(
        dict(_report(),
             advisor_tool_calls=[{"type": "web_search", "query": "宮古島の方言の意味"}]),
        followup="調べてきたよ、こういう意味だって",
    )
    core = Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), brains={"primary_brain": brain},
    )

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
    core = _core({"primary_brain": brain}, registry=_single_registry())

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
    core = _core({"primary_brain": brain}, registry=_single_registry())

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
    core = _core(
        {"primary_brain": primary, "fallback_brain": ScriptedBrain(_report())},
        memory=_BrokenMemory(),
    )

    result = core.turn_routed("散歩が好きなんだ", now=NOW)

    assert result.report.reply, "記憶が壊れていても会話の返答は届くべき"
    assert primary.call_count == 1


def main() -> None:
    tests = [
        test_normal_turn_uses_primary_brain,
        test_single_brain_registry_never_crashes_when_primary_fails,
        test_communication_error_falls_back_but_does_not_tighten_rule,
        test_contract_format_violation_does_not_tighten_rule,
        test_never_crashes_when_fallback_extraction_always_fails,
        test_never_crashes_when_fallback_returns_malformed_report,
        test_normal_chat_ignores_advisor_tool_calls_from_converse,
        test_streaming_callbacks_reach_brain_and_fire_in_order,
        test_think_rules_skip_judge_for_casual_and_explicit_utterances,
        test_memory_failure_does_not_break_conversation,
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

    core = _core({"primary_brain": BodyBrain()}, registry=_single_registry())
    core.perceive(catalog)
    chosen: list = []

    core.turn_routed("こんにちは", now=NOW, on_body=chosen.append)
    core.turn_routed("こんにちは", now=NOW)  # 体を受け取る先がなければ、体は聞かない

    assert seen == [catalog, None]
    assert chosen == [BodyChoice(expression="喜び")]


def test_the_reply_core_gives_itself_is_announced_like_a_brain_reply() -> None:
    """脳が返答を出せず既定の返答になっても、記録に残す返答として1回知らせる。"""
    fired: list[str] = []
    core = _core({"primary_brain": ScriptedBrain(raise_error=True)}, registry=_single_registry())

    result = core.turn_routed("こんにちは", now=NOW, on_reply=fired.append)

    assert fired == [result.report.reply]
