"""persona 可変ブロック自律改訂（設計書 §4.3）のテスト。固定ブロックは不可・1回20%まで・前の文を控える。"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.persona_revise import (
    compute_block_change_ratio,
    revise_persona_block,
)
from mind.core.protection import ChangeLog, GenerationStore, ProtectionError
from mind.core.persona_assets import load_persona_assets
from mind.core.idea import PERSONA_DIR


def _copy_persona_dir(tmp: Path) -> Path:
    src = PERSONA_DIR
    dest = tmp / "persona"
    shutil.copytree(src, dest)
    return dest


def _stores(tmp: Path) -> tuple[ChangeLog, GenerationStore]:
    return ChangeLog(tmp / "changes.jsonl"), GenerationStore(tmp / "generations.jsonl")


def test_fixed_block_write_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        persona_dir = _copy_persona_dir(Path(tmpdir))
        change_log, generation_store = _stores(Path(tmpdir))
        try:
            revise_persona_block(
                "core_principles",
                "改変",
                reason="テスト",
                change_log=change_log,
                generation_store=generation_store,
                persona_dir=persona_dir,
            )
            raise AssertionError("固定ブロックへの書き込みが通ってしまった")
        except ProtectionError:
            pass


def test_change_ratio_over_20_percent_rejected() -> None:
    """改訂幅上限は2026-07-23にマスター判断で40%→20%へ引き下げ
    （`core/protection.py`の`MAX_AUTONOMOUS_CHANGE_RATIO`）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        persona_dir = _copy_persona_dir(Path(tmpdir))
        assets = load_persona_assets(persona_dir)
        block = next(b for b in assets.blocks if b.id == "personality")
        change_log, generation_store = _stores(Path(tmpdir))
        drastically_different = "全" * max(len(block.text), 100)
        ratio = compute_block_change_ratio(block.text, drastically_different)
        assert ratio > 0.2
        try:
            revise_persona_block(
                "personality",
                drastically_different,
                reason="大幅改訂",
                change_log=change_log,
                generation_store=generation_store,
                persona_dir=persona_dir,
            )
            raise AssertionError("20%超が通ってしまった")
        except ProtectionError:
            pass


def test_mutable_block_revise_keeps_the_old_text_and_reports() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        persona_dir = _copy_persona_dir(Path(tmpdir))
        assets = load_persona_assets(persona_dir)
        block = next(b for b in assets.blocks if b.id == "voice")
        before = block.text
        n = max(1, len(before) // 10)
        new_content = "微調整。" + before[n:]
        ratio = compute_block_change_ratio(before, new_content)
        assert ratio <= 0.4
        change_log, generation_store = _stores(Path(tmpdir))

        revise_persona_block(
            "voice",
            new_content,
            reason="口調の微調整",
            change_log=change_log,
            generation_store=generation_store,
            persona_dir=persona_dir,
        )
        assert generation_store.has_persona_block("voice")
        assert len(change_log.read_all()) == 1
        reloaded = (persona_dir / block.file).read_text(encoding="utf-8")
        assert reloaded == new_content
