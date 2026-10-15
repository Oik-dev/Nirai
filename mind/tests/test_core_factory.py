"""実運用向けCore組み立て(create_core)のテスト。設計書 §5.5-4, §3.1。

LLM不要（ネットワーク呼び出しはしない。Brainのインスタンス化と構造のみ検査する）。
長期記憶は、このプロセスの住人のイデア（テストでは使い捨てのイデア）を読む。索引がまだなければ、何も思い出さない記憶になる。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.brains.ollama.adapter import OllamaAdapter
from mind.brains.gemini import GeminiAdapter
from mind.core.factory import create_core
from mind.core.idea import IDEA
from mind.core.runtime import Core


def test_create_core_builds_full_core() -> None:
    core = create_core()

    assert isinstance(core, Core)
    assert core.persona_text  # イデアの persona/ から結合できている
    assert core.absolute_rules  # role=absolute_rules（06_boundary.md）
    assert core.thresholds is not None
    assert core.memory is not None and core.memory.idea == IDEA
    assert core.memory.index.pages == {}  # 使い捨てのイデアには、まだ記憶がない
    assert {e.name for e in core.registry} == {"serina-gemma4-unc", "gemini-3.8-flash",
                                               "gemini-3.7-flash", "gemini-3.6-flash",
                                               "gemini-3-flash-preview", "gemini-3.5-flash-lite"}
    assert isinstance(core.brains["serina-gemma4-unc"], OllamaAdapter)
    assert isinstance(core.brains["gemini-3.8-flash"], GeminiAdapter)
    assert core.home_marks is not None


def test_build_brain_rejects_unknown_adapter() -> None:
    """未知のadapter種別は明示的にValueErrorで弾く（config/brains.toml誤記の早期検知）。"""
    from mind.core.config import load_thresholds
    from mind.core.factory import _build_brain
    from mind.core.routing.registry import BrainEntry

    bad_entry = BrainEntry("x", "unknown", "local", "small")
    try:
        _build_brain(bad_entry, load_thresholds())
    except ValueError:
        pass
    else:
        raise AssertionError("未知のadapter種別はValueErrorであるべき")
