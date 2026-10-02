"""調査用 debug.jsonl（AI 手渡し）の回帰。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core import debug_log


def test_emit_writes_jsonl_without_utterance(tmp_path: Path) -> None:
    log_path = tmp_path / "debug.jsonl"
    debug_log.configure(log_path)
    try:
        debug_log.emit(
            kind="pulse",
            action="fire",
            pulse_kind="emotion",
            reason="sustained_mood",
            axis="信頼",
            value=0.63,
        )
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["kind"] == "pulse"
        assert row["action"] == "fire"
        assert row["pulse_kind"] == "emotion"
        assert row["axis"] == "信頼"
        assert "utterance" not in row
        assert "reply" not in row
        assert "ts" in row
    finally:
        debug_log.configure(None)


def test_clip_long_fields(tmp_path: Path) -> None:
    log_path = tmp_path / "debug.jsonl"
    debug_log.configure(log_path)
    try:
        debug_log.emit(kind="turn", action="error", detail="x" * 500)
        row = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert len(row["detail"]) <= debug_log._MAX_FIELD_CHARS
        assert row["detail"].endswith("…")
    finally:
        debug_log.configure(None)


def test_read_recent(tmp_path: Path) -> None:
    log_path = tmp_path / "debug.jsonl"
    debug_log.configure(log_path)
    try:
        for i in range(5):
            debug_log.emit(kind="turn", action="error", phase=str(i))
        recent = debug_log.read_recent(2)
        assert len(recent) == 2
        assert recent[0]["phase"] == "3"
        assert recent[1]["phase"] == "4"
    finally:
        debug_log.configure(None)
