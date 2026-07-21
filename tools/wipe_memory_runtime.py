#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""記憶ランタイムの範囲3 wipe（記憶正本入れ直し用）。

必須:
    1. Serina GUI を停止する
    2. python tools/backup_db.py
    3. python tools/wipe_memory_runtime.py --i-understand

残す: change_log.jsonl / generations.jsonl / quota / routing / summaries / persona / legacy
消す: memories系・会話帳簿・facts・chore_box・diary/emotion/pulse/persona_propose state
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3

import sqlite_vec

from serina.core.memory.protection import DEFAULT_CHANGE_LOG_PATH, ChangeLog, ChangeReport

DATA = ROOT / "data"
DEFAULT_DB = DATA / "serina_memory.db"

TABLES_TO_CLEAR = (
    "memory_tombstones",
    "distillation_keys",
    "facts",
    "history",
    "archived_history",
    "sessions",
    "memories",
)

STATE_FILES_TO_DELETE = (
    DATA / "chore_box.db",
    DATA / "diary_state.json",
    DATA / "emotion_state.json",
    DATA / "pulse_state.json",
    DATA / "persona_propose_state.json",
)


def wipe_memory_runtime(db_path: Path = DEFAULT_DB, *, change_log: ChangeLog | None = None) -> dict:
    """範囲3 wipe を実行し、件数サマリを返す。"""
    if not db_path.exists():
        raise FileNotFoundError(f"DB が無い: {db_path}")

    summary: dict = {"db": str(db_path), "cleared_tables": {}, "deleted_files": []}
    conn = sqlite3.connect(str(db_path))
    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        # ベクトル表は先に空にする
        try:
            cur = conn.execute("DELETE FROM memory_vec")
            summary["cleared_tables"]["memory_vec"] = cur.rowcount
        except sqlite3.OperationalError as exc:
            summary["cleared_tables"]["memory_vec"] = f"skip:{exc}"
        for table in TABLES_TO_CLEAR:
            try:
                cur = conn.execute(f"DELETE FROM {table}")
                summary["cleared_tables"][table] = cur.rowcount
            except sqlite3.OperationalError as exc:
                summary["cleared_tables"][table] = f"skip:{exc}"
        conn.commit()
    finally:
        conn.close()

    for path in STATE_FILES_TO_DELETE:
        if path.exists():
            path.unlink()
            summary["deleted_files"].append(str(path))

    log = change_log or ChangeLog(DEFAULT_CHANGE_LOG_PATH)
    log.record(
        ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action="記憶正本入れ直し（範囲3 wipe）",
            target_id=0,
            reason="マスター承認: 出来の悪い記憶DB・履歴・固定9を破棄し遺産から入れ直す",
            before=str(summary),
            after=None,
        )
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="記憶ランタイム範囲3 wipe")
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help="破壊操作を理解したうえで実行する（必須）",
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    if not args.i_understand:
        print("拒否: --i-understand が必要です。先に backup_db.py を実行してください。", file=sys.stderr)
        return 2
    summary = wipe_memory_runtime(args.db)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
