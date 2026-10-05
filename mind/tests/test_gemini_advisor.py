"""Gemini アドバイザー（Wave 7 C4）の結合・回帰テスト。"""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.core.config import ThresholdsConfig
from mind.core.factory import create_core
from mind.core.intake.advisor_tools import execute_advisor_tool_calls
from mind.core.intake.gate import process_report
from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import load_brain_registry
from mind.core.runtime import Core
from mind.core.state.routing_rules import RoutingRules
from mind.skills.gemini_advisor.client import (
    ANTIGRAVITY_AGENT,
    DEFAULT_MODEL,
    WEB_MODEL_FALLBACK_CHAIN,
    default_gemini_call_with_fallback,
    extract_interaction_text,
    model_for_category,
)
from mind.skills.gemini_advisor.payload import FORBIDDEN_PAYLOAD_KEYS
from mind.skills.gemini_advisor.skill import GeminiAdvisorSkill, load_gemini_advisor


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig()


def test_advisor_request_excludes_persona_and_memory() -> None:
    captured: list[dict] = []

    def mock_call(body: dict) -> str:
        captured.append(body)
        return "晴れ"

    skill = GeminiAdvisorSkill(api_key="test-key", call_fn=mock_call)
    skill.consult("明日の東京の天気", category="web_search", routing_rules=RoutingRules())

    assert captured, "API が呼ばれる"
    body_text = json.dumps(captured[0], ensure_ascii=False)
    assert "persona.md" not in body_text
    assert "boundary.md" not in body_text
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in captured[0]

    user_part = captured[0]["contents"][0]["parts"][0]["text"]
    assert user_part == "明日の東京の天気"
    assert "セリナの人格" not in user_part
    assert "記憶" not in user_part
    sys_inst = captured[0]["systemInstruction"]["parts"][0]["text"]
    assert "無人格" in sys_inst
    assert "tools" in captured[0]
    assert "google_search" in captured[0]["tools"][0]


def test_process_report_never_executes_advisor_from_brain_output() -> None:
    """関所は外部通信を一切行わない（2026-07-26 A1）。

    Brainが幻覚で道具の呼び出しを書いても、process_report経由では
    外聞きが実行されない。外聞きの実行主体はCore（事実レーン）のみ
    （`test_full_pipeline_proposal_gate_advisor_followup`が実行経路の統合検証を担う）。
    """
    calls: list[dict] = []
    skill = GeminiAdvisorSkill(api_key="k", call_fn=lambda body: calls.append(body) or "x")
    raw = {
        "reply": "調べてみるね",
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "advisor_tool_calls": [
            {"type": "advisor_consult", "query": "明日の東京の天気", "category": "web_search"},
        ],
    }
    result = process_report(raw)
    assert not calls, "process_reportは外部通信を一切行わない（skill.consultが呼ばれない）"
    assert result.advisor_tool_outcome is None

    # skillは未使用のまま（参照だけ保持して呼ばれていないことを明示）
    assert skill.enabled


def test_no_key_skips_advisor_conversation_continues() -> None:
    """APIキー無し（advisor無効）でもCore経由の会話ターンは正常に返る。"""
    chat_responses = ["そのまま答えるね"]

    def ollama_call(prompt: str) -> str:
        if "needs_deep_thinking" in prompt:
            return '```json\n{"needs_deep_thinking": false, "reason": "test"}\n```'
        if "【あなたが今返した言葉】" in prompt:
            return "{}"
        return chat_responses[0]

    registry = load_brain_registry()
    brain = OllamaAdapter(chat_call_fn=ollama_call)
    skill = load_gemini_advisor(api_key=None)
    assert not skill.enabled

    core = Core(
        persona_text="セリナの人格テスト",
        absolute_rules="機微を漏らさない",
        thresholds=_thresholds(),
        registry=registry,
        quota_ledger=QuotaLedger(),
        routing_rules=RoutingRules(),
        brains={"serina-gemma4-unc": brain},
        gemini_advisor=skill,
    )

    result = core.turn_routed("明日の天気教えて", now=datetime.now(timezone.utc))
    assert result.report.reply == "そのまま答えるね"
    assert not (result.advisor_tool_outcome and result.advisor_tool_outcome.executed)


def test_sensitive_query_not_sent_to_cloud() -> None:
    captured: list[dict] = []

    skill = GeminiAdvisorSkill(
        api_key="k",
        call_fn=lambda body: captured.append(body) or "ok",
    )
    rules = RoutingRules()
    rules.tighten("09012345678")

    outcome = execute_advisor_tool_calls(
        [{"type": "advisor_consult", "query": "09012345678の住所"}],
        skill,
        routing_rules=rules,
    )
    assert not outcome.executed
    assert not captured


def test_full_pipeline_gemini_window_single_message() -> None:
    """統合パイプライン（2026-07-31改訂）: 提案→関所→Gemini窓口を無言で解決→Voice1回で返す。

    旧「保留文→2通目」機構は廃止済み（Phase D）。Gemini呼びかけの解決は
    Voice（converse）を呼ぶより前に完了しており、その結果（材料）がpack経由で
    Voiceの唯一の生成呼び出しへ渡る。
    """
    advisor_answers: list[str] = []

    def gemini_call(body: dict) -> str:
        advisor_answers.append(body["contents"][0]["parts"][0]["text"])
        return "晴れ"

    converse_prompts: list[str] = []

    def ollama_call(prompt: str) -> str:
        if "【あなたが今返した言葉】" in prompt:
            return "{}"
        converse_prompts.append(prompt)
        return "明日は晴れだよ！Geminiお姉ちゃんに聞いてきたよ"

    registry = load_brain_registry()
    brain = OllamaAdapter(chat_call_fn=ollama_call)
    skill = GeminiAdvisorSkill(api_key="k", call_fn=gemini_call)
    core = Core(
        persona_text="セリナの人格テスト",
        absolute_rules="機微を漏らさない",
        thresholds=_thresholds(),
        registry=registry,
        quota_ledger=QuotaLedger(),
        routing_rules=RoutingRules(),
        brains={"serina-gemma4-unc": brain},
        gemini_advisor=skill,
    )

    result = core.turn_routed("Geminiに明日の天気教えて", now=datetime.now(timezone.utc))

    assert advisor_answers == ["Geminiに明日の天気教えて"]
    assert result.report.reply == "明日は晴れだよ！Geminiお姉ちゃんに聞いてきたよ"
    assert result.citations is None, "Gemini材料はcitationsを使わない"
    assert result.advisor_tool_outcome is not None
    assert result.advisor_tool_outcome.executed

    # 窓口の結果確定後にVoiceが1回だけ呼ばれ、材料がpack（渡されたプロンプト）に含まれる。
    assert len(converse_prompts) == 1
    assert "Geminiからの回答" in converse_prompts[0]
    assert "晴れ" in converse_prompts[0]


def test_create_core_without_api_key_starts() -> None:
    tmp = Path(tempfile.mkdtemp())
    core = create_core(gemini_env_path=tmp / "missing.env")
    assert core.gemini_advisor is not None
    assert not core.gemini_advisor.enabled


def test_create_core_wires_tavily_search_without_api_key() -> None:
    """Phase D: create_core は tavily_search も（キー無しでも無効Skillとして）配線する。"""
    tmp = Path(tempfile.mkdtemp())
    core = create_core(gemini_env_path=tmp / "missing.env", tavily_env_path=tmp / "missing.env")
    assert core.tavily_search is not None
    assert not core.tavily_search.enabled


def test_payload_audit_dict_has_no_forbidden_keys() -> None:
    skill = GeminiAdvisorSkill(api_key="k", call_fn=lambda b: "x")
    skill.consult("テスト質問", routing_rules=RoutingRules())
    audit = skill.last_request_body
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in audit


def test_model_for_category_routes_all_to_antigravity() -> None:
    assert model_for_category("web_search") == ANTIGRAVITY_AGENT
    assert model_for_category("general") == ANTIGRAVITY_AGENT
    assert model_for_category("code_qa") == ANTIGRAVITY_AGENT
    # 枠復活時の再配線用に定数は残す
    assert WEB_MODEL_FALLBACK_CHAIN == (
        "gemini-3.5-flash",
        "gemini-3-flash-preview",
        "gemini-3.1-flash-lite",
    )
    assert DEFAULT_MODEL == WEB_MODEL_FALLBACK_CHAIN[0]
    assert ANTIGRAVITY_AGENT.startswith("antigravity")


def test_gemini_fallback_skips_retryable_models(monkeypatch) -> None:  # noqa: ANN001
    import requests
    from mind.skills.gemini_advisor import client as client_mod

    calls: list[str] = []

    def fake_call(api_key: str, model: str, body: dict, *, timeout: float) -> str:
        calls.append(model)
        if model != "gemini-3.1-flash-lite":
            resp = requests.Response()
            resp.status_code = 429
            raise requests.HTTPError("429", response=resp)
        return "高市早苗"

    monkeypatch.setattr(client_mod, "default_gemini_call", fake_call)
    text, used = default_gemini_call_with_fallback(
        "k",
        WEB_MODEL_FALLBACK_CHAIN,
        {"contents": []},
        timeout=1.0,
    )
    assert text == "高市早苗"
    assert used == "gemini-3.1-flash-lite"
    assert calls == list(WEB_MODEL_FALLBACK_CHAIN)


def test_consult_refuses_when_routing_rules_missing() -> None:
    """門番未接続（routing_rules=None）は既定拒否。素通しの直呼び穴を塞ぐ。"""
    captured: list[dict] = []
    skill = GeminiAdvisorSkill(api_key="k", call_fn=lambda body: captured.append(body) or "x")

    assert skill.consult("明日の天気") is None
    assert not captured
    assert "門番" in (skill.last_failure_reason or "")


def test_sanitize_query_fail_closed_when_routing_rules_missing() -> None:
    """Phase C2是正: sanitize_query単体（payload.pyレベル）でもrouting_rules=Noneはfail-closed。

    test_consult_refuses_when_routing_rules_missing はconsult()レベル（GeminiAdvisorSkill
    側の早期リターン）の確認。本テストはpayload.py側の関所コード自体の契約を、
    将来の別の呼び出し元に対しても確認する（2026-07-31改訂）。
    """
    from mind.skills.gemini_advisor.payload import sanitize_query

    assert sanitize_query("明日の天気", routing_rules=None) is None


def test_consult_records_failure_reasons() -> None:
    """機微拒否・通信失敗・成功で last_failure_reason が区別できる。"""
    rules = RoutingRules()
    rules.tighten("秘密の合言葉")

    blocked = GeminiAdvisorSkill(api_key="k", call_fn=lambda body: "x")
    assert blocked.consult("秘密の合言葉って何だっけ", routing_rules=rules) is None
    assert "機微" in (blocked.last_failure_reason or "")

    def boom(body: dict) -> str:
        raise RuntimeError("接続失敗")

    failed = GeminiAdvisorSkill(api_key="k", call_fn=boom)
    assert failed.consult("天気", routing_rules=rules) is None
    assert "RuntimeError" in (failed.last_failure_reason or "")

    ok = GeminiAdvisorSkill(api_key="k", call_fn=lambda body: "ok")
    assert ok.consult("天気", routing_rules=rules) == "ok"
    assert ok.last_failure_reason is None


def test_antigravity_prod_path_tools_and_body(monkeypatch) -> None:  # noqa: ANN001
    """本番（Interactions）経路の荷姿監査: category別ツールとクエリのみが載る。"""
    from mind.skills.gemini_advisor import skill as skill_mod

    captured: dict = {}

    def fake_antigravity(api_key, agent, query, *, timeout, tools=None):  # noqa: ANN001
        captured.update({"agent": agent, "query": query, "tools": tools})
        return "回答"

    monkeypatch.setattr(skill_mod, "default_antigravity_call", fake_antigravity)
    skill = GeminiAdvisorSkill(api_key="k")
    rules = RoutingRules()

    assert skill.consult("明日の東京の天気", category="web_search", routing_rules=rules) == "回答"
    assert captured["agent"] == ANTIGRAVITY_AGENT
    assert captured["query"] == "明日の東京の天気"
    assert {"type": "google_search"} in captured["tools"]
    assert {"type": "url_context"} in captured["tools"]
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in skill.last_request_body

    assert skill.consult("この書き方は正しい？", category="code_qa", routing_rules=rules) == "回答"
    assert captured["tools"] == [{"type": "code_execution"}]


def test_antigravity_request_body_shape(monkeypatch) -> None:  # noqa: ANN001
    """Interactions API へ送る JSON の鍵は agent/input/environment/tools のみ。"""
    from mind.skills.gemini_advisor import client as client_mod

    captured: dict = {}

    class FakeResp:
        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return {"status": "completed", "output_text": "答え"}

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: ANN001
        captured["body"] = json
        return FakeResp()

    monkeypatch.setattr(client_mod.requests, "post", fake_post)
    text = client_mod.default_antigravity_call(
        "k", ANTIGRAVITY_AGENT, "天気", timeout=1.0, tools=[{"type": "google_search"}],
    )
    assert text == "答え"
    assert set(captured["body"].keys()) == {"agent", "input", "environment", "tools"}
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in captured["body"]


def test_advisor_turn_budget_stops_stacking() -> None:
    """1ターンの外聞き合計時間に予算を設ける。超過後の相談は捨てて会話を返す。"""
    clock = {"t": 0.0}

    class SlowSkill:
        enabled = True
        last_failure_reason = None

        def consult(self, query, *, category="general", routing_rules=None):  # noqa: ANN001
            clock["t"] += 200.0
            return "遅い回答"

    outcome = execute_advisor_tool_calls(
        [
            {"type": "web_search", "query": "a"},
            {"type": "web_search", "query": "b"},
        ],
        SlowSkill(),
        routing_rules=RoutingRules(),
        turn_budget_seconds=180.0,
        clock=lambda: clock["t"],
    )
    assert len(outcome.executed) == 1
    assert any("予算" in d.get("reason", "") for d in outcome.discarded)


def test_discarded_reason_distinguishes_sensitive_block() -> None:
    """関所の破棄記録が「機微で止めた」と「失敗した」を区別する。"""
    skill = GeminiAdvisorSkill(api_key="k", call_fn=lambda body: "x")
    rules = RoutingRules()
    rules.tighten("秘密の合言葉")

    outcome = execute_advisor_tool_calls(
        [{"type": "advisor_consult", "query": "秘密の合言葉って何"}],
        skill,
        routing_rules=rules,
    )
    assert not outcome.executed
    assert outcome.discarded
    assert "機微" in outcome.discarded[0]["reason"]


class _ScriptedBrain:
    def __init__(self, script: dict) -> None:
        self.script = script

    def converse(self, pack) -> dict:  # noqa: ANN001
        return dict(self.script)


def test_normal_turn_advisor_hallucination_never_reaches_cloud_even_on_retry() -> None:
    """通常会話（事実レーン対象外の発話）は、契約違反→代打の再試行があっても外聞きを一切実行しない。

    2026-07-20時点で自律第3発注（Brainの自己申告による外聞き）は既に退役しており
    （`brains/ollama/adapter.py`のconverse: advisor_tool_calls常に[]）、2026-07-26のA1で
    process_report経由の裏口実行も閉じた。Brainがhallucinationでadvisor_tool_callsを
    書いても（このテストのScriptedBrainのように）、契約リトライを挟んでも0回のまま。
    """
    from mind.core.routing.registry import BrainEntry

    calls: list[dict] = []
    skill = GeminiAdvisorSkill(
        api_key="k", call_fn=lambda body: calls.append(body) or "回答",
    )
    advisor_calls = [{"type": "web_search", "query": "明日の天気"}]
    # self_assessment 欠落 → 契約違反で fallback へ
    bad = {"reply": "下書き", "advisor_tool_calls": list(advisor_calls)}
    good = {
        "reply": "有効な返答",
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "advisor_tool_calls": list(advisor_calls),
    }
    registry = [
        BrainEntry("primary_brain", "ollama", "local", "primary", -1, -1, "small"),
        BrainEntry("fallback_brain", "ollama", "local", "fallback", -1, -1, "small"),
    ]
    core = Core(
        persona_text="人格",
        absolute_rules="ルール",
        thresholds=_thresholds(),
        registry=registry,
        quota_ledger=QuotaLedger(),
        routing_rules=RoutingRules(),
        brains={"primary_brain": _ScriptedBrain(bad), "fallback_brain": _ScriptedBrain(good)},
        gemini_advisor=skill,
    )

    # 事実レーンに乗らない発話（天気・検索マーカー等を含まない）であることが前提。
    result = core.turn_routed("宮古島の方言の意味を教えて", now=datetime.now(timezone.utc))

    assert result.report.reply == "有効な返答"
    assert not calls, "通常会話ターンから外聞きは一度も実行されない"


def test_extract_interaction_text_from_model_output_step() -> None:
    data = {
        "status": "completed",
        "steps": [
            {"type": "thought", "summary": [{"text": "thinking"}]},
            {
                "type": "model_output",
                "content": [{"type": "text", "text": "答えはこれ"}],
            },
        ],
    }
    assert extract_interaction_text(data) == "答えはこれ"
