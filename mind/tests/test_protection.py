"""イデアを守る3原則（core/protection.py）のテスト。設計書 §4.3。

記憶のページの「本人の言葉は書き換えない」は tests/test_episodic_memory.py、人格の改訂の関所は tests/test_persona_revise.py。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.protection import (
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
    assert_persona_block_writable,
)


def test_persona_fixed_block_writable_rejected() -> None:
    with pytest.raises(ProtectionError):
        assert_persona_block_writable(mutable=False)


def test_persona_mutable_block_writable_allowed() -> None:
    assert_persona_block_writable(mutable=True)


def test_change_log_keeps_reports_with_page_ids_and_old_numeric_ids(tmp_path: Path) -> None:
    """原則1: 変更レポートは追記だけ。ページの id（文字）も、これまでの数の目印も読める。"""
    log = ChangeLog(tmp_path / "changes.jsonl")
    log.record(ChangeReport("2026-10-04T00:00:00+00:00", "Masterが発言を削除", "ep-2026-10-03-01", "手動", "{}", None))
    log.record(ChangeReport("2026-10-04T00:00:01+00:00", "personaブロック改訂", 900_002, "見直し", "前", "後"))
    assert [r.target_id for r in log.read_all()] == ["ep-2026-10-03-01", 900_002]


def test_generation_store_keeps_the_persona_block_before_rewrite(tmp_path: Path) -> None:
    """原則2: 人格ブロックを書き換える前の文を控える。"""
    store = GenerationStore(tmp_path / "generations.jsonl")
    assert not store.has_persona_block("voice")
    store.save_persona_block("voice", "前の口調")
    assert store.has_persona_block("voice")
