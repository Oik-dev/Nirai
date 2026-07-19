"""人格の刃（§3.7）のユニットテスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.pulse import build_pulse_prompt, PulseGenerationContext
from serina.core.config import load_thresholds
from serina.core.chores.idle_policy import PulseCandidate
from serina.core.persona.blade import apply_visible_brake, persona_contains_teasing_tolerance
from serina.core.persona_assets import load_persona_assets
from serina.core.state.emotion import EmotionState


def test_persona_contains_teasing_tolerance_marker() -> None:
    assets = load_persona_assets()
    assert persona_contains_teasing_tolerance(assets.persona_text)


def test_visible_brake_parenthetical() -> None:
    out = apply_visible_brake("元の文", mode="parenthetical", filtered=True)
    assert out.startswith("（フィルタ）")
    assert "元の文" in out


def test_visible_brake_none_leaves_text() -> None:
    assert apply_visible_brake("元の文", mode="none", filtered=True) == "元の文"


def test_visible_brake_skipped_when_not_filtered() -> None:
    assert apply_visible_brake("元の文", mode="parenthetical", filtered=False) == "元の文"


def test_pulse_prompt_includes_persona_not_hardcoded_message() -> None:
    assets = load_persona_assets()
    ctx = PulseGenerationContext(
        candidate=PulseCandidate(kind="time", trigger_id="t1", context={"idle_minutes": 50}),
        persona_text=assets.persona_text,
        absolute_rules=assets.absolute_rules,
        prefs_summary="好み",
        relation_summary="関係",
        emotion=EmotionState(),
        thresholds=load_thresholds(),
    )
    prompt = build_pulse_prompt(ctx)
    assert "からかい許容度" in prompt
    assert "能動 Pulse" in prompt
    assert "マスターへ能動的に" in prompt
