"""人格ブロック結合（設計書 §4.3）。実効ソースはイデアの persona/ のみ（テストでは fixtures/persona の写し）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.persona_assets import load_persona_assets


def test_load_yields_nonempty_persona_and_rules() -> None:
    assets = load_persona_assets()
    assert assets.persona_text.strip()
    assert assets.absolute_rules.strip()
    assert "セリナ" in assets.persona_text or "わたし" in assets.persona_text


def test_manifest_evidence_weeks() -> None:
    assets = load_persona_assets()
    by_id = {b.id: b for b in assets.blocks}
    assert by_id["core_principles"].evidence_weeks == 3
    assert by_id["identity"].evidence_weeks is None
    assert by_id["boundary"].evidence_weeks is None
    assert by_id["personality"].evidence_weeks == 1
    assert by_id["voice"].evidence_weeks == 1
    assert by_id["love"].evidence_weeks == 1
    assert by_id["boundary"].role == "absolute_rules"


def test_block_order_matches_manifest() -> None:
    assets = load_persona_assets()
    assert [b.id for b in assets.blocks] == [
        "core_principles",
        "identity",
        "personality",
        "voice",
        "love",
        "boundary",
    ]
