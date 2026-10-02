"""評価レポート読込・ack・要注意判定の単体テスト。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.eval_report import (
    build_claude_copy_text,
    load_eval_report,
    report_needs_attention,
    save_eval_ack,
)


def test_report_needs_attention_only_when_fail_unacked(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    ack_path = tmp_path / "ack.json"
    report = {
        "ran_at": "2026-07-20T00:00:00+00:00",
        "fail_count": 1,
        "fails": [{"name": "想起ヒット率", "status": "fail", "detail": "低い"}],
        "metrics": [],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    assert report_needs_attention(report, {}) is True
    save_eval_ack(report["ran_at"], path=ack_path)
    from serina.core import eval_report as er

    ack = er.load_eval_ack(ack_path)
    assert report_needs_attention(report, ack) is False


def test_load_missing_report_returns_none(tmp_path: Path) -> None:
    assert load_eval_report(tmp_path / "nope.json") is None


def test_claude_copy_includes_fails() -> None:
    text = build_claude_copy_text(
        {
            "ran_at": "t",
            "mode": "live",
            "fail_count": 1,
            "fails": [{"name": "誤想起率", "status": "fail", "detail": "混入"}],
        }
    )
    assert "誤想起率" in text
    assert "eval_suite.py --live" in text
