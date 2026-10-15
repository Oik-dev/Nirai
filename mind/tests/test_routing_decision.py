"""毎ターンのBrain選択のテスト。設計書 §3.2。

家の話題は手元の脳だけ。それ以外は使えるクラウドを登録簿の順に並べ、最後に手元の脳。
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.decision import decide_brain, decide_brains
from mind.core.routing.registry import BrainEntry

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def _single_registry() -> list[BrainEntry]:
    return [BrainEntry("serina-gemma4-unc", "ollama", "local", "small")]


def _multi_registry() -> list[BrainEntry]:
    return [
        BrainEntry("cloud_a", "gemini", "cloud", "large"),
        BrainEntry("cloud_b", "gemini", "cloud", "large"),
        BrainEntry("gemma", "ollama", "local", "small"),
    ]


def test_home_topic_uses_the_local_brain() -> None:
    result = decide_brain(registry=_single_registry(), now=NOW)
    assert result == "serina-gemma4-unc"


def test_a_dead_cloud_is_skipped_and_the_local_brain_comes_last() -> None:
    result = decide_brains(
        registry=_multi_registry(), now=NOW, topic_home=False,
        is_alive=lambda name: name != "cloud_a",
    )
    assert result == ["cloud_b", "gemma"]
