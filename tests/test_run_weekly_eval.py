"""週次評価スクリプトの曜日ガード・レポート書式。"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))

import run_weekly_eval as weekly  # noqa: E402


def test_skip_when_not_sunday(tmp_path: Path) -> None:
    monday = datetime(2026, 7, 20)  # 月曜
    assert monday.weekday() == 0
    with patch.object(weekly, "_is_sunday", return_value=False):
        assert weekly.run_weekly(force=False, skip_wait=True, live=False) is None


def test_skip_when_already_ran_today(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    report_path = tmp_path / "eval_latest_report.json"
    today = datetime.now().astimezone()
    report_path.write_text(
        json.dumps({"ran_at": today.isoformat(), "fail_count": 0, "metrics": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(weekly, "REPORT_PATH", report_path)
    with patch.object(weekly, "_is_sunday", return_value=True):
        assert weekly.run_weekly(force=False, skip_wait=True, live=False) is None


def test_force_writes_report(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    report_path = tmp_path / "eval_latest_report.json"
    monkeypatch.setattr(weekly, "REPORT_PATH", report_path)
    monkeypatch.setattr(weekly, "_maybe_export_life", lambda: None)
    with patch.object(weekly, "_ollama_reachable", return_value=True):
        payload = weekly.run_weekly(force=True, skip_wait=True, live=False)
    assert payload is not None
    assert report_path.exists()
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert "metrics" in saved
    assert saved["fail_count"] == 0 or isinstance(saved["fail_count"], int)
    assert len(saved["metrics"]) == 9
