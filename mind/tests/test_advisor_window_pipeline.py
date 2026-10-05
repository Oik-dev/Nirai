"""Phase D 無言統合パイプラインの受け入れ基準テスト（計画書「契約（受け入れ基準）」節）。

2026-07-05／07-20 事故（判定とVoice生成の分離不足で人格が変質）の再発防止が主眼。
ここでは計画書が明示した6つの受け入れ基準を機械的に検証する:
1. Voice（converse）呼び出しが、Gemini/Tavilyいずれの窓口についても、結果が確定する前に
   一度も呼ばれていないこと
2. 窓口が失敗・拒否・タイムアウトしたとき、報告書に実行済みフラグが立たないこと
   （Voiceの自由文そのものは検査できないため、材料が無いことで裏付ける）
3. Tavily材料があるときのcitationsがCore組み立てのURL文字列と一致し、reply自体には
   URLが含まれないこと
4. セッション履歴（会話の記録）として渡る文字列がreplyのみで、citationsが混入しないこと
5. Gemini窓口が統合パイプライン内でもrouting_rulesを実際に受け取っていること（注意3の回帰）
6. 両窓口ともタイムアウト／ヒット無し／拒否時に、通常会話と同じ形で報告書が返ること
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.config import ThresholdsConfig
from mind.core.routing.registry import BrainEntry
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.runtime import Core
from mind.core.state.routing_rules import RoutingRules
from mind.skills.gemini_advisor.skill import GeminiAdvisorSkill
from mind.skills.tavily_search.skill import TavilyResult, TavilySearchSkill

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig()


def _single_registry() -> list[BrainEntry]:
    return [BrainEntry("primary_brain", "ollama", "local", "primary", -1, -1, "small")]


def _core(**kwargs) -> Core:
    return Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=RoutingRules(),
        **kwargs,
    )


def _report(reply: str = "了解です") -> dict:
    return {
        "reply": reply,
        "self_assessment": {"over_capacity": False, "reason": "テスト"},
    }


class _OrderedBrain:
    """judge/converseの呼び出し順を共有リストへ記録するフェイクBrain。"""

    def __init__(self, call_order: list[str], *, judge_response: dict) -> None:
        self.call_order = call_order
        self.judge_response = judge_response

    def judge(self, prompt: str) -> dict:
        self.call_order.append("judge")
        return dict(self.judge_response)

    def converse(self, pack, *, think=False, on_token=None, on_reply=None) -> dict:  # noqa: ANN001
        self.call_order.append("converse")
        return _report()


class _OrderedGeminiSkill:
    def __init__(self, call_order: list[str]) -> None:
        self.call_order = call_order
        self.enabled = True
        self.last_failure_reason = None

    def consult(self, query: str, *, category: str = "general", routing_rules=None) -> str:  # noqa: ANN001
        self.call_order.append("gemini_consult")
        return "回答本文"


class _OrderedTavilySkill:
    def __init__(self, call_order: list[str]) -> None:
        self.call_order = call_order
        self.enabled = True
        self.last_failure_reason = None

    def search(self, query: str, *, routing_rules=None) -> TavilyResult:  # noqa: ANN001
        self.call_order.append("tavily_search")
        return TavilyResult(answer="ダミー回答", results=[])


# ---------------------------------------------------------------------------
# 1. converseは窓口の結果確定より前に呼ばれない
# ---------------------------------------------------------------------------


def test_converse_never_called_before_gemini_window_settles() -> None:
    call_order: list[str] = []
    brain = _OrderedBrain(call_order, judge_response={"needs_deep_thinking": False})
    core = _core(brains={"primary_brain": brain}, gemini_advisor=_OrderedGeminiSkill(call_order))

    core.turn_routed("Geminiに教えて", now=NOW)

    assert "gemini_consult" in call_order
    assert call_order.index("gemini_consult") < call_order.index("converse")
    assert call_order.count("converse") == 1


def test_converse_never_called_before_tavily_window_settles() -> None:
    call_order: list[str] = []
    brain = _OrderedBrain(
        call_order, judge_response={"needs_search": True, "query": "検索クエリ"},
    )
    core = _core(brains={"primary_brain": brain}, tavily_search=_OrderedTavilySkill(call_order))

    core.turn_routed("最近のニュースってどうなってる？", now=NOW)

    assert call_order == ["judge", "tavily_search", "converse"], (
        "Tavily判定(judge)→検索実行→converseの順で1回ずつ確定させる"
    )


# ---------------------------------------------------------------------------
# 2. 窓口が失敗・拒否・タイムアウトしたとき、実行済みフラグが立たない
# ---------------------------------------------------------------------------


def test_gemini_unavailable_leaves_no_executed_outcome_and_guards_the_lie() -> None:
    """Voiceが自由文で嘘をついても（モックで意図的に再現）、材料自体が無いことを確認する。"""
    brain = _report_capturing_brain(reply="Geminiが教えてくれたよ！（本当は聞いていない嘘）")
    core = _core(brains={"primary_brain": brain}, gemini_advisor=None)

    result = core.turn_routed("Geminiに明日の天気教えて", now=NOW)

    assert result.advisor_tool_outcome is None, "Gemini無効時は実行済みフラグを立てない"
    assert result.citations is None
    pack = brain.received_packs[0]
    assert "聞いた・調べたという体で話さない" in pack.advisor_context_text
    assert "Geminiからの回答" not in pack.advisor_context_text


def test_gemini_empty_answer_is_treated_as_miss_not_material() -> None:
    """Minor（serina-code-reviewer指摘）: Gemini consultが空文字を返す経路（契約上は到達しない
    想定だが consult() の戻り型は str | None で空文字を排除しない）でも、材料として扱わない。"""

    class EmptyAnswerGeminiSkill:
        enabled = True
        last_failure_reason = None

        def consult(self, query: str, *, category: str = "general", routing_rules=None) -> str:  # noqa: ANN001
            return ""

    brain = _report_capturing_brain(reply="普通に返事するね")
    core = _core(brains={"primary_brain": brain}, gemini_advisor=EmptyAnswerGeminiSkill())

    result = core.turn_routed("Geminiに聞いてみて", now=NOW)

    pack = brain.received_packs[0]
    assert "聞いた・調べたという体で話さない" in pack.advisor_context_text
    assert "Geminiからの回答" not in pack.advisor_context_text


def test_tavily_search_exception_leaves_no_citations_and_guards_the_lie() -> None:
    class BoomTavilySkill:
        enabled = True
        last_failure_reason = "RuntimeError: boom"

        def search(self, query: str, *, routing_rules=None) -> None:  # noqa: ANN001
            return None

    brain = _report_capturing_brain(reply="調べてきたよ（本当は失敗している嘘）")
    judge_brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": True, "query": "q"}, inner=brain,
    )
    core = _core(brains={"primary_brain": judge_brain}, tavily_search=BoomTavilySkill())

    result = core.turn_routed("最近の為替どうなってる？", now=NOW)

    assert result.citations is None
    pack = brain.received_packs[0]
    assert "聞いた・調べたという体で話さない" in pack.advisor_context_text
    assert "検索結果" not in pack.advisor_context_text


# ---------------------------------------------------------------------------
# 3. Tavily材料のcitationsはCore組み立てのURLと一致し、replyにURLは含まれない
# ---------------------------------------------------------------------------


def test_tavily_hit_citations_match_urls_and_reply_has_no_url() -> None:
    class HitTavilySkill:
        enabled = True
        last_failure_reason = None

        def search(self, query: str, *, routing_rules=None) -> TavilyResult:  # noqa: ANN001
            return TavilyResult(
                answer="東京は明日晴れです。",
                results=[
                    {"title": "天気予報", "url": "https://example.com/tokyo-weather", "snippet": "晴れ"},
                ],
            )

    brain = _report_capturing_brain(reply="明日は晴れるみたいだよ")
    judge_brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": True, "query": "東京 明日 天気"}, inner=brain,
    )
    core = _core(brains={"primary_brain": judge_brain}, tavily_search=HitTavilySkill())

    result = core.turn_routed("明日の東京の天気ってどうなるかな", now=NOW)

    assert result.citations == [{"url": "https://example.com/tokyo-weather"}]
    assert "https://example.com/tokyo-weather" not in result.report.reply
    pack = brain.received_packs[0]
    assert "検索結果" in pack.advisor_context_text
    assert "東京は明日晴れです。" in pack.advisor_context_text


def test_tavily_unsafe_url_scheme_is_dropped_from_citations() -> None:
    """serina-code-reviewer指摘: 外部（Tavily）由来の未検証URLがそのままhrefになる経路を
    塞ぐ。http/https以外のスキーマ（javascript:等）はcitationsから除外する。"""

    class HitTavilySkill:
        enabled = True
        last_failure_reason = None

        def search(self, query: str, *, routing_rules=None) -> TavilyResult:  # noqa: ANN001
            return TavilyResult(
                answer="回答",
                results=[
                    {"title": "安全", "url": "https://example.com/ok", "snippet": "s"},
                    {"title": "危険", "url": "javascript:alert(1)", "snippet": "s"},
                ],
            )

    brain = _report_capturing_brain(reply="見つかったよ")
    judge_brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": True, "query": "q"}, inner=brain,
    )
    core = _core(brains={"primary_brain": judge_brain}, tavily_search=HitTavilySkill())

    result = core.turn_routed("何か調べて", now=NOW)

    assert result.citations == [{"url": "https://example.com/ok"}]


# ---------------------------------------------------------------------------
# 4. セッション履歴（会話の記録）はreplyのみ（citationsは混入しない）
# ---------------------------------------------------------------------------


def test_citations_do_not_leak_into_the_conversation_flow() -> None:
    """出典（URL）は画面の注記だけ。手元の会話の流れ（＝眠りの間に記憶になる記録の元）には混ざらない。"""
    class HitTavilySkill:
        enabled = True
        last_failure_reason = None

        def search(self, query: str, *, routing_rules=None) -> TavilyResult:  # noqa: ANN001
            return TavilyResult(
                answer="回答本文",
                results=[{"title": "t", "url": "https://example.com/secret-url", "snippet": "s"}],
            )

    brain = _report_capturing_brain(reply="見つかったよ")
    judge_brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": True, "query": "q"}, inner=brain,
    )
    core = _core(brains={"primary_brain": judge_brain}, tavily_search=HitTavilySkill())

    core.turn_routed("何か調べて教えて", now=NOW)

    session_texts = [t.text for t in core.session.turns]
    assert all("https://example.com/secret-url" not in t for t in session_texts)


# ---------------------------------------------------------------------------
# 5. Gemini窓口は統合パイプライン内でもrouting_rulesを実際に受け取っている（注意3回帰）
# ---------------------------------------------------------------------------


def test_gemini_window_receives_routing_rules_in_unified_pipeline() -> None:
    """routing_rulesが機微語で拒否設定されていれば、統合パイプライン経由でもGeminiへ
    送信されないこと（=routing_rulesが実際にexecute_advisor_tool_callsへ渡っている証拠）。
    """
    sent: list[dict] = []

    def gemini_call(body: dict) -> str:
        sent.append(body)
        return "回答"

    rules = RoutingRules()
    rules.tighten("Geminiに極秘の暗証番号を聞いて")

    brain = OllamaAdapter(chat_call_fn=lambda p: _fake_ollama_call(p))
    skill = GeminiAdvisorSkill(api_key="k", call_fn=gemini_call)
    core = Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), quota_ledger=QuotaLedger(), routing_rules=rules,
        brains={"primary_brain": brain}, gemini_advisor=skill,
    )

    result = core.turn_routed("Geminiに極秘の暗証番号を聞いて", now=NOW)

    assert not sent, "機微語でtightenされたrouting_rulesが配線されていればGeminiへ送信されない"
    assert result.advisor_tool_outcome is not None
    assert not result.advisor_tool_outcome.executed


# ---------------------------------------------------------------------------
# 6. 両窓口ともタイムアウト／ヒット無し／拒否時に、通常会話と同じ形で報告書が返る
# ---------------------------------------------------------------------------


def test_tavily_needs_search_false_returns_normal_report_shape() -> None:
    brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": False, "query": ""},
        inner=_report_capturing_brain(reply="ただの雑談だよ"),
    )
    core = _core(brains={"primary_brain": brain})

    result = core.turn_routed("今日は天気がいいね", now=NOW)

    assert result.report.reply == "ただの雑談だよ"
    assert result.citations is None


def test_gemini_call_word_but_advisor_disabled_returns_normal_report_shape() -> None:
    brain = _report_capturing_brain(reply="普通に返事するね")
    core = _core(brains={"primary_brain": brain}, gemini_advisor=None)

    result = core.turn_routed("Geminiに聞いてみてよ", now=NOW)

    assert result.report.reply == "普通に返事するね"
    assert result.citations is None


# ---------------------------------------------------------------------------
# 7. 窓口解決自体が例外を投げても§3.2最終防衛線（沈黙しない）が保たれる
#    （advisor指摘・completion-review前是正: 窓口解決はループ外のtry/exceptの外にあるため、
#     _resolve_advisor_window自体を個別に握らないと、この例外だけが素通しになっていた）
# ---------------------------------------------------------------------------


def test_advisor_window_resolution_exception_does_not_break_conversation() -> None:
    class ExplodingRoutingRules:
        def is_sensitive(self, text: str) -> bool:  # noqa: ANN001
            raise RuntimeError("routing_rules boom")

    class HitTavilySkill:
        enabled = True
        last_failure_reason = None

        def search(self, query: str, *, routing_rules=None) -> TavilyResult:  # noqa: ANN001
            return TavilyResult(answer="x", results=[])

    brain = _JudgeAndConverseBrain(
        judge_response={"needs_search": True, "query": "検索クエリ"},
        inner=_report_capturing_brain(reply="通常どおり返事するね"),
    )
    core = Core(
        persona_text="人格", absolute_rules="ルール", thresholds=_thresholds(),
        registry=_single_registry(), quota_ledger=QuotaLedger(),
        routing_rules=ExplodingRoutingRules(),
        brains={"primary_brain": brain}, tavily_search=HitTavilySkill(),
    )

    result = core.turn_routed("何か調べて教えて", now=NOW)

    assert result.report.reply, "窓口解決が例外を投げても沈黙しない（§3.2最終防衛線）"
    assert result.citations is None


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


class _ReportCapturingBrain:
    """converseに渡されたpackを記録し、固定replyを返すフェイクBrain。"""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.received_packs: list = []

    def converse(self, pack, *, think=False, on_token=None, on_reply=None) -> dict:  # noqa: ANN001
        self.received_packs.append(pack)
        return _report(self.reply)


def _report_capturing_brain(reply: str) -> _ReportCapturingBrain:
    return _ReportCapturingBrain(reply)


class _JudgeAndConverseBrain:
    """judgeは固定応答、converseは内側のBrain（pack記録用）へ委譲するフェイクBrain。"""

    def __init__(self, judge_response: dict, inner: _ReportCapturingBrain) -> None:
        self.judge_response = judge_response
        self.inner = inner

    def judge(self, prompt: str) -> dict:
        return dict(self.judge_response)

    def converse(self, pack, *, think=False, on_token=None, on_reply=None) -> dict:  # noqa: ANN001
        return self.inner.converse(pack, think=think, on_token=on_token, on_reply=on_reply)

    @property
    def received_packs(self) -> list:
        return self.inner.received_packs


def _fake_ollama_call(prompt: str) -> str:
    if "【あなたが今返した言葉】" in prompt:
        return "{}"
    return "了解です"
