"""常駐要約ブロック（§3.4 / §4-12）のテスト。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.summaries import (
    BLOCK_PREFS,
    BLOCK_RELATION,
    MAX_BLOCK_CHARS,
    MAX_TOTAL_CHARS,
    enforce_summary_limits,
    load_summary_blocks,
    update_summary_block,
)
from mind.core.memory.protection import ChangeLog


def _stores(tmp: Path) -> tuple[Path, ChangeLog]:
    blocks_path = tmp / "blocks.json"
    change_log = ChangeLog(tmp / "changes.jsonl")
    return blocks_path, change_log


def test_each_block_respects_800_char_limit() -> None:
    long_text = "あ" * 900
    prefs, relation = enforce_summary_limits(long_text, "関係")
    assert len(prefs) <= MAX_BLOCK_CHARS
    assert len(relation) <= MAX_BLOCK_CHARS


def test_total_respects_3000_char_limit() -> None:
    prefs = "好" * 800
    relation = "係" * 800
    folded_prefs, folded_relation = enforce_summary_limits(prefs, relation)
    assert len(folded_prefs) + len(folded_relation) <= MAX_TOTAL_CHARS


def test_update_summary_block_records_change_report() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        blocks_path, change_log = _stores(Path(tmpdir))
        update_summary_block(
            BLOCK_PREFS,
            "コーヒーが好き",
            change_log=change_log,
            reason="蒸留から更新",
            path=blocks_path,
        )
        blocks = load_summary_blocks(blocks_path)
        assert blocks.prefs_summary == "コーヒーが好き"
        reports = change_log.read_all()
        assert len(reports) == 1
        assert reports[0].reason == "蒸留から更新"


def test_update_folds_when_total_exceeds_limit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        blocks_path, change_log = _stores(Path(tmpdir))
        update_summary_block(
            BLOCK_PREFS,
            "好" * 800,
            change_log=change_log,
            reason="prefs",
            path=blocks_path,
        )
        update_summary_block(
            BLOCK_RELATION,
            "係" * 800,
            change_log=change_log,
            reason="relation",
            path=blocks_path,
        )
        blocks = load_summary_blocks(blocks_path)
        assert len(blocks.prefs_summary) + len(blocks.relation_summary) <= MAX_TOTAL_CHARS
