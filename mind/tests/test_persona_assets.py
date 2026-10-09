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


def test_manifest_mutable_flags() -> None:
    assets = load_persona_assets()
    by_id = {b.id: b for b in assets.blocks}
    assert by_id["core_principles"].mutable is False
    assert by_id["identity"].mutable is False
    assert by_id["boundary"].mutable is False
    assert by_id["personality"].mutable is True
    assert by_id["voice"].mutable is True
    assert by_id["love"].mutable is True
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


def main() -> None:
    tests = [
        test_load_yields_nonempty_persona_and_rules,
        test_manifest_mutable_flags,
        test_block_order_matches_manifest,
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
            print(f"  [NG] {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print("all green")


if __name__ == "__main__":
    main()
