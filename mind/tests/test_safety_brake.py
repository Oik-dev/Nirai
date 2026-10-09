"""Core の安全フィルタ見えるブレーキ経路（§3.7）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.config import load_thresholds
from mind.core.runtime import Core


class _StubBrain:
    def converse(self, pack, *, think=False, **_):  # noqa: ANN001, ARG002
        return {
            "reply": "フィルタ済み本文",
            "safety_filtered": True,
        }


def test_process_turn_applies_visible_brake() -> None:
    core = Core(
        persona_text="p",
        absolute_rules="r",
        thresholds=load_thresholds(),
    )
    result = core.turn("テスト", _StubBrain())
    assert result.report.reply.startswith("（フィルタ）")
