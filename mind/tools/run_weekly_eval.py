# -*- coding: utf-8 -*-
"""日曜・Serina.bat 起動3分後の評価セット自動実行（設計書 §5.2）。

Serina.bat がバックグラウンドで本スクリプトを起動する。
既定: 日曜以外は何もしない。同一ローカル日に既にレポートがあれば再実行しない。
起動直後の負荷を避けるため180秒待つ。結果は data/eval_latest_report.json（GUIが読む）。

手動確認:
    python tools/run_weekly_eval.py --force --no-wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from mind.core.eval_report import DEFAULT_REPORT_PATH as REPORT_PATH  # noqa: E402
from mind.core.idea import DATA_DIR, LIFE_DIR  # noqa: E402

DEFAULT_WAIT_SECONDS = 180


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_sunday(*, now: datetime | None = None) -> bool:
    current = now or datetime.now().astimezone()
    return current.weekday() == 6  # Monday=0 … Sunday=6


def _local_date_key(when: datetime | None = None) -> str:
    current = when or datetime.now().astimezone()
    return current.astimezone().date().isoformat()


def _report_already_ran_today(path: Path | None = None, *, today: str | None = None) -> bool:
    """同一ローカル日にレポートがあれば True（Serina 多重起動の二重実行防止）。"""
    target = path or REPORT_PATH
    if not target.exists():
        return False
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
        ran_at = payload.get("ran_at")
        if not ran_at:
            return False
        ran = datetime.fromisoformat(str(ran_at))
        if ran.tzinfo is None:
            ran = ran.replace(tzinfo=timezone.utc)
        key = today or _local_date_key()
        return ran.astimezone().date().isoformat() == key
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False


def _ollama_reachable(base_url: str = "http://127.0.0.1:11434") -> bool:
    try:
        import requests

        response = requests.get(f"{base_url}/api/tags", timeout=5.0)
        return response.ok
    except Exception:  # noqa: BLE001
        return False


def _maybe_export_life() -> None:
    """可視成長用に life/ と週次ログが無ければ一度 export を試す。"""
    life_dir = LIFE_DIR
    weekly = DATA_DIR / "eval_life_weekly.json"
    if life_dir.exists() and any(life_dir.glob("**/*.md")) and weekly.exists():
        return
    import subprocess

    script = ROOT / "tools" / "export_life.py"
    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        print(f"export_life exit={completed.returncode}", flush=True)
        if completed.stdout:
            print(completed.stdout.strip(), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"export_life 見送り: {exc}", flush=True)


def build_report(results: list, *, mode: str) -> dict:
    metrics = [
        {"name": r.name, "status": r.status, "detail": r.detail}
        for r in results
    ]
    fails = [m for m in metrics if m["status"] == "fail"]
    skipped = [m for m in metrics if m["status"] == "skipped"]
    return {
        "ran_at": _utc_now_iso(),
        "mode": mode,
        "metrics": metrics,
        "fail_count": len(fails),
        "skipped_count": len(skipped),
        "fails": fails,
    }


def write_report(payload: dict, path: Path | None = None) -> Path:
    target = path or REPORT_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def run_weekly(
    *,
    force: bool = False,
    wait_seconds: int = DEFAULT_WAIT_SECONDS,
    live: bool = True,
    skip_wait: bool = False,
) -> dict | None:
    if not force and not _is_sunday():
        print("日曜ではないため評価をスキップします（--force で強制可）", flush=True)
        return None

    if not force and _report_already_ran_today():
        print("本日すでに評価済みのためスキップします（--force で再実行可）", flush=True)
        return None

    if not skip_wait and wait_seconds > 0:
        print(f"{wait_seconds}秒待機（Serina起動直後の負荷回避）…", flush=True)
        time.sleep(wait_seconds)

    if live and not _ollama_reachable():
        report = {
            "ran_at": _utc_now_iso(),
            "mode": "live",
            "metrics": [],
            "fail_count": 1,
            "skipped_count": 0,
            "fails": [
                {
                    "name": "Ollama疎通",
                    "status": "fail",
                    "detail": "Ollama に接続できないため評価を中止",
                }
            ],
        }
        write_report(report)
        print("Ollama未起動のため fail レポートを書きました", flush=True)
        return report

    _maybe_export_life()

    from eval_suite import run_eval_suite

    results = run_eval_suite(live=live, strict=False)
    report = build_report(results, mode="live" if live else "dry")
    write_report(report)
    print(f"レポート書込: {REPORT_PATH}", flush=True)
    for m in report["metrics"]:
        print(f"[{m['status'].upper():7}] {m['name']}: {m['detail']}", flush=True)
    print(
        f"fail={report['fail_count']} skipped={report['skipped_count']}",
        flush=True,
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Serina 週次評価（日曜・Serina.bat起動）")
    parser.add_argument(
        "--force",
        action="store_true",
        help="日曜以外・本日済みでも実行する",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=DEFAULT_WAIT_SECONDS,
        help=f"起動後の待機秒（既定{DEFAULT_WAIT_SECONDS}）",
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="待機せずすぐ実行（--force 煙測向け）",
    )
    parser.add_argument(
        "--dry",
        action="store_true",
        help="--live なし（決定論指標のみ。通常は使わない）",
    )
    args = parser.parse_args()
    run_weekly(
        force=args.force,
        wait_seconds=args.wait_seconds,
        live=not args.dry,
        skip_wait=args.no_wait,
    )


if __name__ == "__main__":
    main()
