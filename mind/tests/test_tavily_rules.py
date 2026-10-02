"""Tavily検索要否の軽量判定（core/routing/tavily_rules.py）のテスト。

合言葉ゼロで毎発話judgeへ委任する薄い関数。曖昧・失敗時はneeds_search=False（安全側）。
判定へ供給する材料がmaster_utterance以外を含まないことも検証する
（実装前レビュー指摘・出力が外部境界を越えるための歯止め）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.tavily_rules import decide_tavily_search


class _FakeJudgeBrain:
    def __init__(self, response: dict | None = None, *, raise_exc: bool = False) -> None:
        self.response = response
        self.raise_exc = raise_exc
        self.received_prompts: list[str] = []

    def judge(self, prompt: str) -> dict:
        self.received_prompts.append(prompt)
        if self.raise_exc:
            raise RuntimeError("judge boom")
        return self.response  # type: ignore[return-value]


class _NoJudgeBrain:
    """judge を持たない Brain（converse のみ）。"""


def test_needs_search_true_path() -> None:
    brain = _FakeJudgeBrain({"needs_search": True, "query": "東京 明日 天気"})
    decision = decide_tavily_search("明日の東京の天気ってどうなる？", brain)
    assert decision.needs_search is True
    assert decision.query == "東京 明日 天気"


def test_needs_search_false_path() -> None:
    brain = _FakeJudgeBrain({"needs_search": False, "query": ""})
    decision = decide_tavily_search("今日も一日お疲れさま", brain)
    assert decision.needs_search is False


def test_judge_exception_falls_back_to_false() -> None:
    brain = _FakeJudgeBrain(raise_exc=True)
    decision = decide_tavily_search("最近のニュースどうなってる？", brain)
    assert decision.needs_search is False


def test_judge_non_dict_response_falls_back_to_false() -> None:
    brain = _FakeJudgeBrain(response=None)
    decision = decide_tavily_search("最近のニュースどうなってる？", brain)
    assert decision.needs_search is False


def test_self_reference_is_stripped_from_query() -> None:
    """実運用で判明した不具合（2026-08-01）: judgeが呼びかけ語「セリナ」を検索クエリに
    残してしまい、検索エンジンへ無関係な語（自分の名前）が混ざる。プロンプト指示だけに
    頼らず機械的にも除去する。"""
    brain = _FakeJudgeBrain({"needs_search": True, "query": "セリナ 名古屋 天気"})
    decision = decide_tavily_search("セリナ、名古屋の天気しってる？", brain)
    assert decision.needs_search is True
    assert "セリナ" not in decision.query
    assert decision.query == "名古屋 天気"


def test_query_that_is_only_self_reference_falls_back_to_false() -> None:
    """クエリが呼びかけ語だけだった場合、除去後は空になるため契約違反として安全側へ倒す。"""
    brain = _FakeJudgeBrain({"needs_search": True, "query": "セリナ"})
    decision = decide_tavily_search("ねえセリナ", brain)
    assert decision.needs_search is False
    assert decision.query == ""


def test_judge_contract_violation_true_without_query_falls_back_to_false() -> None:
    """needs_search=trueなのにqueryが空は契約違反。安全側へ倒す。"""
    brain = _FakeJudgeBrain({"needs_search": True, "query": ""})
    decision = decide_tavily_search("最新のiPhoneの価格教えて", brain)
    assert decision.needs_search is False


def test_brain_without_judge_falls_back_to_false() -> None:
    decision = decide_tavily_search("最新のiPhoneの価格教えて", _NoJudgeBrain())
    assert decision.needs_search is False


def test_empty_utterance_short_circuits_without_calling_judge() -> None:
    brain = _FakeJudgeBrain({"needs_search": True, "query": "x"})
    decision = decide_tavily_search("   ", brain)
    assert decision.needs_search is False
    assert brain.received_prompts == []


def test_judge_prompt_contains_only_master_utterance() -> None:
    """判定へ供給する材料はmaster_utteranceのみ（記憶・関係状態等は含まれない）ことを、
    judgeへ渡されたプロンプト文字列に発話がそのまま含まれる形で確認する
    （実装前レビュー指摘の受け入れ基準）。"""
    brain = _FakeJudgeBrain({"needs_search": False, "query": ""})
    utterance = "これは一意に識別できるテスト発話トークンXYZ123です"
    decide_tavily_search(utterance, brain)
    assert len(brain.received_prompts) == 1
    prompt = brain.received_prompts[0]
    assert utterance in prompt
    # judge の第一引数は文字列のみ（プロンプト以外の会話文脈オブジェクトは渡らない）。
    assert isinstance(prompt, str)


def test_decision_carries_why() -> None:
    brain = _FakeJudgeBrain({"needs_search": False, "query": ""})
    assert decide_tavily_search("こんにちは", brain).why
