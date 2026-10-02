"""Core.turn_routed の配線テスト。設計書 §3.2(決定論チェックリスト), §3.5(フォールバック)

2026-07-18: Brain構成刷新（合意台帳 §9）。品質昇格機構(current_tier/§3.4)は§9.2で
廃止済みのため、旧escalation関連テストは削除した。fallback関連のテストはCore.turn_routedの
一般的な耐障害機構（§3.2最終防衛線・§3.5フォールバック作法）を検証するもので、role制
スキーマ自体は将来の複数Brain運用再開に備えて維持されている（§9.1）ため、
primary+fallbackの2Brain登録簿で引き続き検証する。実運用のBrain単一構成
（fallback役が登録簿に存在しない）での収束は_single_registry系のテストで別途確認する。
"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.brains.contract.schema import CloudRejectionError
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.runtime import Core
from serina.core.state.routing_rules import RoutingRules

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _registry() -> list[BrainEntry]:
    """primaryをcloud・fallbackをlocalに置く（CloudRejectionError系テストの前提。
    §3.5のtighten判定はlocation=="cloud"時のみ発火する）。"""
    return [
        BrainEntry("primary_brain", "ollama", "cloud", "primary", -1, -1, "small"),
        BrainEntry("fallback_brain", "ollama", "local", "fallback", -1, -1, "small"),
    ]


def _single_registry() -> list[BrainEntry]:
    """実運用（Brain単一構成・config/brains.toml）と同型。fallback役が登録簿に存在しない。"""
    return [BrainEntry("primary_brain", "ollama", "local", "primary", -1, -1, "small")]


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1)


class ScriptedBrain:
    def __init__(
        self, script: dict | None = None, raise_error: bool = False, raise_cls: type[Exception] = RuntimeError,
    ) -> None:
        self.script = script
        self.raise_error = raise_error
        self.raise_cls = raise_cls
        self.call_count = 0

    def converse(self, pack) -> dict:  # noqa: ANN001
        self.call_count += 1
        if self.raise_error:
            raise self.raise_cls("Brain呼び出しが失敗した")
        return self.script


def _report(over_capacity: bool, fusen_list: list[dict] | None = None) -> dict:
    return {
        "reply": "了解です",
        "fusen_list": fusen_list or [],
        "self_assessment": {"over_capacity": over_capacity, "reason": "テスト"},
    }


def _core(brains: dict, memory_store: MemoryStore | None = None, registry: list[BrainEntry] | None = None) -> Core:
    return Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=registry or _registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(), brains=brains,
        memory_store=memory_store,
    )


class _BrokenEmbedder(OllamaEmbedder):
    def __init__(self) -> None:
        def always_fail(model: str, text: str) -> list[float]:
            raise RuntimeError("Ollamaが瞬断した")
        super().__init__(call_fn=always_fail)


def test_normal_turn_uses_primary_brain() -> None:
    primary = ScriptedBrain(_report(over_capacity=False))
    fallback = ScriptedBrain(_report(over_capacity=False))
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


def test_cloud_rejection_falls_back_without_tightening_rule() -> None:
    """会話クラウド拒否→tightenは退役。代打のみ行い門番は研がない。"""
    primary = ScriptedBrain(raise_error=True, raise_cls=CloudRejectionError)
    fallback = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("危険な話題かもしれない発言", now=NOW)

    assert fallback.call_count == 1
    assert result.report.reply == "了解です"
    assert not core.routing_rules.is_sensitive("危険な話題かもしれない発言")


def test_communication_error_falls_back_but_does_not_tighten_rule() -> None:
    """§3.5: 通信エラー・弾切れは同ターン代打のみ。安全フィルタの拒否と違いラチェットは研がない"""
    primary = ScriptedBrain(raise_error=True, raise_cls=ConnectionError)
    fallback = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("ただの雑談のつもりの発言", now=NOW)

    assert fallback.call_count == 1
    assert result.report.reply == "了解です"
    assert not core.routing_rules.is_sensitive("ただの雑談のつもりの発言"), (
        "通信エラーで振り分けルールを研いではいけない（無実の話題が誤ってセンシティブ扱いされる）"
    )


def test_contract_format_violation_does_not_tighten_rule() -> None:
    """§3.5: 単純な契約書式違反（安全フィルタ拒否ではない）もラチェット対象外"""
    malformed = ScriptedBrain({"reply": "", "fusen_list": [], "self_assessment": {"over_capacity": False, "reason": "x"}})
    fallback = ScriptedBrain(_report(over_capacity=False))
    core = _core({"primary_brain": malformed, "fallback_brain": fallback})

    result = core.turn_routed("書式が崩れるだけの発言", now=NOW)

    assert fallback.call_count == 1
    assert result.report.reply == "了解です"
    assert not core.routing_rules.is_sensitive("書式が崩れるだけの発言"), (
        "契約書式違反だけで振り分けルールを研いではいけない"
    )


def test_never_crashes_when_fallback_extraction_always_fails() -> None:
    """§3.2最終防衛線: primaryが拒否→fallback代打も全滅(adapter raise)しても沈黙しない"""
    class RaisingBrain:
        def converse(self, pack):  # noqa: ANN001
            raise RuntimeError("完全に応答不能")

    primary = ScriptedBrain(raise_error=True)
    fallback = RaisingBrain()
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


def test_never_crashes_when_fallback_returns_malformed_report() -> None:
    """§3.2最終防衛線: fallbackが書式違反の報告書を返しても沈黙しない"""
    class MalformedBrain:
        def converse(self, pack):  # noqa: ANN001
            return {"reply": "", "fusen_list": [], "self_assessment": {"over_capacity": "いいえ", "reason": "x"}}

    primary = ScriptedBrain(raise_error=True)
    fallback = MalformedBrain()
    core = _core({"primary_brain": primary, "fallback_brain": fallback})

    result = core.turn_routed("危険な話題", now=NOW)

    assert result.report.reply, "何らかの返答が返るべき（沈黙しない）"


class _FakeAdvisorSkill:
    """execute_advisor_tool_calls が要求する最小の顔（enabled / consult）。"""

    enabled = True
    last_failure_reason = None

    def consult(self, query: str, *, category: str = "general", routing_rules=None) -> str:  # noqa: ANN001
        return f"回答:{query}"


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

    def converse(self, pack, *, think=False, on_token=None, on_reply=None) -> dict:  # noqa: ANN001
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
    """通常会話は外聞きしない（旧自律第3発注廃止）。converse が advisor_tool_calls を
    返しても followup は生えない。外聞きは事実レーンのみ。
    """
    brain = _StreamingBrain(
        dict(_report(over_capacity=False),
             advisor_tool_calls=[{"type": "web_search", "query": "宮古島の方言の意味"}]),
        followup="調べてきたよ、こういう意味だって",
    )
    core = Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        brains={"primary_brain": brain}, gemini_advisor=_FakeAdvisorSkill(),
    )

    result = core.turn_routed("宮古島の方言ってどういう意味？ちょっと教えて", now=NOW)

    assert result.report.reply == "了解です"
    texts = [t.text for t in core.session.turns]
    assert texts == [
        "宮古島の方言ってどういう意味？ちょっと教えて",
        "了解です",
    ]


def test_gemini_window_resolves_before_single_converse_call() -> None:
    """統合パイプライン（2026-07-31改訂）: Gemini呼びかけは無言でCoreが解決し、
    結果が確定してからVoice（converse）を1回だけ呼ぶ（保留文・2通目は廃止）。
    拘束条件4: 窓口の結果確定前にconverseが呼ばれていないことをここで確認する。
    """
    brain = _StreamingBrain(dict(_report(over_capacity=False), reply="晴れ20度だよ"))
    core = Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        brains={"primary_brain": brain}, gemini_advisor=_FakeAdvisorSkill(),
    )
    tokens: list[str] = []
    replies: list[str] = []

    result = core.turn_routed(
        "Geminiに今日の東京の天気教えて",
        now=NOW,
        on_token=tokens.append,
        on_reply=replies.append,
    )

    assert brain.received_think == [False], "converseはちょうど1回だけ呼ばれる"
    assert result.report.reply == "晴れ20度だよ", "Voiceの1回の生成がそのまま最終回答"
    assert "".join(tokens) == "晴れ20度だよ"
    assert replies == ["晴れ20度だよ"]
    assert result.citations is None, "Gemini材料はcitationsを使わない"

    # Gemini窓口はconverseより前に確定しており、その結果（材料）がpackへ渡っている。
    assert len(brain.received_packs) == 1
    pack = brain.received_packs[0]
    assert "Geminiからの回答" in pack.advisor_context_text
    assert "回答:Geminiに今日の東京の天気教えて" in pack.advisor_context_text

    texts = [t.text for t in core.session.turns]
    assert texts == ["Geminiに今日の東京の天気教えて", "晴れ20度だよ"], (
        "citations・指示文はセッション履歴（記憶蒸留材料）に混入しない"
    )


def test_streaming_callbacks_reach_brain_and_fire_in_order() -> None:
    tokens: list[str] = []
    fired: list[str] = []
    brain = _StreamingBrain(_report(over_capacity=False))
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
    brain = _StreamingBrain(_report(over_capacity=False))
    core = _core({"primary_brain": brain}, registry=_single_registry())

    core.turn_routed("おはよう", now=NOW)
    assert brain.judge_calls == 1, "Tavily検索要否判定分のみ（think判定はルール即決でjudge不要）"
    assert brain.received_think[-1] is False

    brain.judge_calls = 0
    core.turn_routed("この命題を証明してほしい", now=NOW)
    assert brain.judge_calls == 1, "Tavily検索要否判定分のみ（明示の深考要求もthink判定はルール即決）"
    assert brain.received_think[-1] is True

    brain.judge_calls = 0
    core.turn_routed("これってどう思う？", now=NOW)
    assert brain.judge_calls >= 2, "Tavily判定分＋中間帯はthink判定でもjudgeへ委任すべき"
    assert brain.received_think[-1] is True, "judgeがtrueと答えたらthink ON"


def test_memory_failure_does_not_break_conversation() -> None:
    """§2.4: 裏方便が遅れても会話は壊れない。想起・記憶書き戻しが失敗しても返答は返るべき"""
    db_path = Path(tempfile.mkdtemp()) / "test_memory.db"
    broken_store = MemoryStore(str(db_path), embedder=_BrokenEmbedder(), vector_dim=4)

    primary = ScriptedBrain(_report(
        over_capacity=False,
        fusen_list=[{
            "kind": "記憶候補", "version": 1,
            "content": {"content": "散歩が好き", "type": "fact", "importance": 0.5,
                        "sensitivity_grade": 0, "quote": "散歩が好きなんだ"},
            "confidence": 0.9,
        }],
    ))
    core = _core(
        {"primary_brain": primary, "fallback_brain": ScriptedBrain(_report(False))},
        memory_store=broken_store,
    )

    result = core.turn_routed("散歩が好きなんだ", now=NOW)

    assert result.report.reply, "記憶が壊れていても会話の返答は届くべき"


def main() -> None:
    tests = [
        test_normal_turn_uses_primary_brain,
        test_single_brain_registry_never_crashes_when_primary_fails,
        test_cloud_rejection_falls_back_without_tightening_rule,
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
