"""評価レポートの読込・ack（GUI / 週次ジョブ共用）。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from mind.core.soul import DATA_DIR

DEFAULT_REPORT_PATH = DATA_DIR / "eval_latest_report.json"
DEFAULT_ACK_PATH = DATA_DIR / "eval_report_ack.json"


def load_eval_report(path: Path | None = None) -> dict[str, Any] | None:
    target = path or DEFAULT_REPORT_PATH
    if not target.exists():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_eval_ack(path: Path | None = None) -> dict[str, Any]:
    target = path or DEFAULT_ACK_PATH
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_eval_ack(ran_at: str, *, path: Path | None = None) -> None:
    target = path or DEFAULT_ACK_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"acked_ran_at": ran_at}
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def report_needs_attention(report: dict[str, Any] | None, ack: dict[str, Any] | None = None) -> bool:
    """fail があり、かつ未ackなら True。"""
    if not report:
        return False
    if int(report.get("fail_count") or 0) <= 0:
        return False
    ack = ack if ack is not None else load_eval_ack()
    return ack.get("acked_ran_at") != report.get("ran_at")


def build_claude_copy_text(report: dict[str, Any]) -> str:
    lines = [
        "Serina 評価レポートの修正依頼です。",
        f"実行時刻: {report.get('ran_at', '')}",
        f"mode: {report.get('mode', '')}",
        f"fail_count: {report.get('fail_count', 0)}",
        "",
        "失敗指標:",
    ]
    fails = report.get("fails") or [
        m for m in (report.get("metrics") or []) if m.get("status") == "fail"
    ]
    if not fails:
        lines.append("（なし）")
    else:
        for m in fails:
            lines.append(f"- {m.get('name')}: {m.get('detail')}")
    lines.extend(
        [
            "",
            "再現:",
            "cd D:\\Products\\dev\\serina",
            "python tools\\run_weekly_eval.py --force --no-wait",
            "または: python tests\\eval_suite.py --live",
            "",
            "正典: docs/設計書.md §5.2 / config/eval_thresholds.toml",
        ]
    )
    return "\n".join(lines)
