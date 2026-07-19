"""人格ブロック結合（合意台帳 §3.9）。注入結果が旧 persona.md＋boundary.md と一致すること。"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.persona_assets import load_persona_assets


def test_load_matches_legacy_persona_and_boundary() -> None:
    assets = load_persona_assets()
    legacy_persona = (ROOT / "prompt" / "persona.md").read_text(encoding="utf-8")
    legacy_boundary = (ROOT / "prompt" / "boundary.md").read_text(encoding="utf-8")

    assert assets.persona_text == legacy_persona
    assert assets.absolute_rules == legacy_boundary


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


def test_combined_hash_stable() -> None:
    """回帰用: 結合文字列のハッシュが変わったら意図せぬ書き換え。"""
    assets = load_persona_assets()
    digest = hashlib.sha256(
        (assets.persona_text + "\n" + assets.absolute_rules).encode("utf-8")
    ).hexdigest()
    # 旧ファイル結合と同一であること（値そのものより一致を優先）
    legacy = (
        (ROOT / "prompt" / "persona.md").read_text(encoding="utf-8")
        + "\n"
        + (ROOT / "prompt" / "boundary.md").read_text(encoding="utf-8")
    )
    assert digest == hashlib.sha256(legacy.encode("utf-8")).hexdigest()


def main() -> None:
    tests = [
        test_load_matches_legacy_persona_and_boundary,
        test_manifest_mutable_flags,
        test_combined_hash_stable,
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
