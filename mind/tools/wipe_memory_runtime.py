#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""記憶ランタイムの範囲3 wipe（記憶正本入れ直し用）。

必須:
    1. Serina GUI を停止する
    2. python tools/backup_db.py
    3. python tools/wipe_memory_runtime.py --i-understand

残す: change_log.jsonl / generations.jsonl / quota / routing / summaries / persona / legacy
消す: memories系（memory_vec含む）・会話帳簿・facts（facts_vec含む）・chore_box・
     diary/emotion/pulse/persona_propose/relationship state
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

from mind.core.memory.protection import DEFAULT_CHANGE_LOG_PATH, ChangeLog, ChangeReport
from mind.tools.backup_db import BACKUP_DIR

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
    DATA / "episodic_state.json",
    DATA / "diary_state.json",  # 旧ファイル名（移行前の環境に残っている場合のため一緒に消す）
    DATA / "emotion_state.json",
    DATA / "pulse_state.json",
    DATA / "persona_propose_state.json",
    DATA / "relationship_state.json",  # 2026-07-26 B2是正(指摘I-2): マスター観測も一緒に消す
)


def find_recent_backup(db_path: Path, backup_dir: Path = BACKUP_DIR) -> Path | None:
    """db_path の最終更新以降に取られた控えがあれば、その Path を返す（無ければ None）。

    「消す前に必ず控えを取る」（可逆性・設計書 §4.3）をコード側でも確認するための
    ガード。backup_db.py と同じ命名規則（serina_memory_*.db）の中から探す。
    """
    if not backup_dir.exists() or not db_path.exists():
        return None
    db_mtime = db_path.stat().st_mtime
    candidates = [p for p in backup_dir.glob("serina_memory_*.db") if p.stat().st_mtime >= db_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def wipe_memory_runtime(
    db_path: Path = DEFAULT_DB,
    *,
    change_log: ChangeLog | None = None,
    skip_backup_check: bool = False,
) -> dict:
    """範囲3 wipe を実行し、件数サマリを返す。

    skip_backup_check はテスト専用。実運用（main 経由）では常に控えの実在を確認する。
    """
    if not db_path.exists():
        raise FileNotFoundError(f"DB が無い: {db_path}")
    if not skip_backup_check and find_recent_backup(db_path) is None:
        raise RuntimeError(
            f"控え（backup）が見つかりません: {BACKUP_DIR} に {db_path.name} 更新後の "
            "serina_memory_*.db がありません。先に `python tools/backup_db.py` を実行してください。"
        )

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
        # 2026-07-26 B2是正(指摘I-2): factsを消す前にfacts_vecも空にする（孤児防止）。
        try:
            cur = conn.execute("DELETE FROM facts_vec")
            summary["cleared_tables"]["facts_vec"] = cur.rowcount
        except sqlite3.OperationalError as exc:
            summary["cleared_tables"]["facts_vec"] = f"skip:{exc}"
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
    try:
        summary = wipe_memory_runtime(args.db)
    except RuntimeError as exc:
        print(f"拒否: {exc}", file=sys.stderr)
        return 3
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
