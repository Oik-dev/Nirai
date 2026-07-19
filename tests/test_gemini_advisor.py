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

from serina.brains.qwen.adapter import QwenAdapter
from serina.core.config import ThresholdsConfig
from serina.core.factory import create_core
from serina.core.intake.advisor_tools import execute_advisor_tool_calls
from serina.core.intake.gate import process_report
from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import load_brain_registry
from serina.core.runtime import Core
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState
from serina.core.state.routing_rules import RoutingRules
from serina.skills.gemini_advisor.payload import FORBIDDEN_PAYLOAD_KEYS
from serina.skills.gemini_advisor.skill import GeminiAdvisorSkill, load_gemini_advisor


def _thresholds() -> ThresholdsConfig:
    return ThresholdsConfig(
        fusen_confidence={"default": 0.5, "道具使用": 0.0},
        mood_guard_max_delta_per_turn=0.1,
    )


def test_advisor_request_excludes_persona_and_memory() -> None:
    captured: list[dict] = []

    def mock_call(body: dict) -> str:
        captured.append(body)
        return "晴れ"

    skill = GeminiAdvisorSkill(api_key="test-key", call_fn=mock_call)
    skill.consult("明日の東京の天気", category="web_search")

    assert captured, "API が呼ばれる"
    body_text = json.dumps(captured[0], ensure_ascii=False)
    assert "persona" not in body_text.lower()
    assert "boundary" not in body_text.lower()
    assert "記憶" not in body_text
    assert "persona.md" not in body_text

    user_part = captured[0]["contents"][0]["parts"][0]["text"]
    assert user_part == "明日の東京の天気"
    assert "セリナの人格" not in user_part
    sys_inst = captured[0]["systemInstruction"]["parts"][0]["text"]
    assert "無人格" in sys_inst


def test_gate_advisor_flow_with_mock_api() -> None:
    skill = GeminiAdvisorSkill(
        api_key="k",
        call_fn=lambda body: "明日は晴れです",
    )
    raw = {
        "reply": "調べてみるね",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "advisor_tool_calls": [
            {"type": "advisor_consult", "query": "明日の東京の天気", "category": "web_search"},
        ],
    }
    result = process_report(
        raw,
        emotion=EmotionState(),
        relationship=RelationshipState(),
        thresholds=_thresholds(),
        gemini_advisor=skill,
    )
    assert result.advisor_tool_outcome is not None
    assert len(result.advisor_tool_outcome.executed) == 1
    assert result.advisor_tool_outcome.executed[0]["answer"] == "明日は晴れです"


def test_no_key_skips_advisor_conversation_continues() -> None:
    skill = load_gemini_advisor(api_key=None)
    assert not skill.enabled

    raw = {
        "reply": "そのまま答えるね",
        "fusen_list": [],
        "self_assessment": {"over_capacity": False, "reason": "test"},
        "advisor_tool_calls": [
            {"type": "web_search", "query": "天気"},
        ],
    }
    result = process_report(
        raw,
        emotion=EmotionState(),
        relationship=RelationshipState(),
        thresholds=_thresholds(),
        gemini_advisor=skill,
    )
    assert result.report.reply == "そのまま答えるね"
    assert result.advisor_tool_outcome is not None
    assert result.advisor_tool_outcome.skipped_disabled


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


def test_full_pipeline_proposal_gate_advisor_rephrase() -> None:
    """提案→関所→advisor→セリナ言い直し（モック）。"""
    advisor_answers: list[str] = []

    def gemini_call(body: dict) -> str:
        advisor_answers.append(body["contents"][0]["parts"][0]["text"])
        return "晴れ"

    chat_responses = [
        "ちょっと調べるね",
        '```json\n{"fusen_list": []}\n```',
        '```json\n{"advisor_tool_calls": [{"type": "web_search", "query": "明日の東京の天気"}]}\n```',
        "明日は晴れだよ！",
    ]

    def qwen_call(prompt: str) -> str:
        if "needs_deep_thinking" in prompt:
            return '```json\n{"needs_deep_thinking": false, "reason": "test"}\n```'
        if "advisor_tool_calls" in prompt and "外部アドバイザー" in prompt:
            return chat_responses[2]
        if "アドバイザーからの材料" in prompt:
            return chat_responses[3]
        if "心の動き" in prompt or "fusen_list" in prompt:
            return chat_responses[1]
        return chat_responses[0]

    registry = load_brain_registry()
    brain = QwenAdapter(chat_call_fn=qwen_call)
    skill = GeminiAdvisorSkill(api_key="k", call_fn=gemini_call)
    core = Core(
        persona_text="セリナの人格テスト",
        absolute_rules="機微を漏らさない",
        thresholds=_thresholds(),
        registry=registry,
        quota_ledger=QuotaLedger(),
        routing_rules=RoutingRules(),
        brains={"serina-qwen35-unc": brain},
        gemini_advisor=skill,
    )

    result = core.turn_routed("明日の天気教えて", now=datetime.now(timezone.utc))
    assert advisor_answers == ["明日の東京の天気"]
    assert result.report.reply == "明日は晴れだよ！"
    assert result.advisor_tool_outcome is not None
    assert result.advisor_tool_outcome.executed


def test_create_core_without_api_key_starts() -> None:
    tmp = Path(tempfile.mkdtemp())
    core = create_core(
        memory_db_path=tmp / "m.db",
        chore_box_path=tmp / "c.db",
        gemini_env_path=tmp / "missing.env",
    )
    assert core.gemini_advisor is not None
    assert not core.gemini_advisor.enabled


def test_payload_audit_dict_has_no_forbidden_keys() -> None:
    skill = GeminiAdvisorSkill(api_key="k", call_fn=lambda b: "x")
    skill.consult("テスト質問")
    audit = skill.last_request_body
    for key in FORBIDDEN_PAYLOAD_KEYS:
        assert key not in audit
