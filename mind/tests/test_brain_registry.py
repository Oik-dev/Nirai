"""Brain登録簿の読み込みテスト。設計書 §3.1「ルーティングは表であってコードではない」

config/brains.toml はローカルGemmaと性能順のクラウドGemini 5行。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.registry import load_brain_registry, local_brain


def test_load_registry_returns_single_entry() -> None:
    """脳が二つ登録され、それぞれ違う場所にある。"""
    entries = load_brain_registry()
    assert len(entries) == 6
    assert entries[0].name == "serina-gemma4-unc"
    assert entries[1].name == 'gemini-3.8-flash'
    assert entries[1].location == 'cloud'
    assert [e.name for e in entries[1:]] == [
        'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash',
        'gemini-3-flash-preview', 'gemini-3.5-flash-lite',
    ]


def test_entry_has_expected_location() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    entry = entries["serina-gemma4-unc"]
    assert entry.adapter == "ollama"
    assert entry.location == "local"
    assert local_brain(list(entries.values())) is entry


def test_provider_limits_are_not_in_our_registry() -> None:
    entries = {e.name: e for e in load_brain_registry()}
    assert set(vars(entries["gemini-3.8-flash"])) == {
        'name', 'adapter', 'location', 'context_size', 'model'}
