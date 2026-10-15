"""毎ターンのBrain選択のテスト。設計書 §3.2 / §9（Brain単一・会話クラウド振り分け退役）。

外への相談は Brain 切替ではなく Gemini アドバイザー Skill。
decide_brain は primary の残弾・生死と全滅時 fallback のみ。
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
    return [BrainEntry("serina-gemma4-unc", "ollama", "local", "primary", "small")]


def _multi_registry() -> list[BrainEntry]:
    return [
        BrainEntry("brain_primary", "ollama", "cloud", "primary", "large"),
        BrainEntry("brain_escalation", "ollama", "cloud", "escalation", "large"),
        BrainEntry("brain_fallback", "ollama", "local", "fallback", "small"),
    ]


def test_normal_turn_uses_primary() -> None:
    result = decide_brain(registry=_single_registry(), now=NOW)
    assert result == "serina-gemma4-unc"


def test_multi_registry_dead_primary_falls_back() -> None:
    result = decide_brains(
        registry=_multi_registry(), now=NOW, topic_home=False,
        is_alive=lambda name: name != "brain_primary",
    )
    assert result == ["brain_escalation", "brain_fallback"]


def main() -> None:
    tests = [
        test_normal_turn_uses_primary,
        test_multi_registry_dead_primary_falls_back,
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
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
