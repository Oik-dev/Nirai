"""評価レポート API のスモーク。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.app import gui_server
from serina.core import eval_report as er


def test_eval_report_api_with_fail_badge(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    report_path = tmp_path / "eval_latest_report.json"
    ack_path = tmp_path / "eval_report_ack.json"
    monkeypatch.setattr(er, "DEFAULT_REPORT_PATH", report_path)
    monkeypatch.setattr(er, "DEFAULT_ACK_PATH", ack_path)
    report = {
        "ran_at": "2026-07-20T12:00:00+00:00",
        "mode": "live",
        "fail_count": 1,
        "skipped_count": 0,
        "metrics": [
            {"name": "想起ヒット率", "status": "fail", "detail": "低い"},
        ],
        "fails": [
            {"name": "想起ヒット率", "status": "fail", "detail": "低い"},
        ],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    client = TestClient(gui_server.app)
    res = client.get("/api/eval/report")
    assert res.status_code == 200
    body = res.json()
    assert body["needs_attention"] is True
    assert "想起ヒット率" in body["claude_copy"]

    ack = client.post("/api/eval/ack")
    assert ack.status_code == 200
    assert ack.json()["ok"] is True

    res2 = client.get("/api/eval/report")
    assert res2.json()["needs_attention"] is False
