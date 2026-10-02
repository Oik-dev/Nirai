# -*- coding: utf-8 -*-
"""継承記憶の created_at をメタ／ファイル名の実日付へ戻す。

優先順:
  1. metadata.date（ISO）
  2. metadata.diary_date（例: 2025年03月21日）
  3. source ファイル名中の YYYYMMDD
  4. 取れなければ 2025-12-01T00:00:00+09:00

対象行は created_at が 2026-06-28（一括投入日）のものだけ。
本番で蒸留された記憶の本物の日時は上書きしない。

使い方:
  python tools/backfill_memory_created_at.py          # dry-run
  python tools/backfill_memory_created_at.py --apply   # 本番適用（backup_db を自動実行してから UPDATE）
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mind.core.idea import DATA_DIR  # noqa: E402
from tools.backup_db import backup_db  # noqa: E402

DB_PATH = DATA_DIR / "serina_memory.db"
JST = ZoneInfo("Asia/Tokyo")
FALLBACK = datetime(2025, 12, 1, 0, 0, 0, tzinfo=JST)
CHANGE_REPORT_PATH = DATA_DIR / "change_reports" / "backfill_memory_created_at.json"


def _normalize_to_jst_iso(raw: str) -> str | None:
    s = str(raw).strip()
    if not s:
        return None
    try:
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=JST)
            return dt.astimezone(JST).isoformat()
    except ValueError:
        pass
    m = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", s)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return datetime(d.year, d.month, d.day, tzinfo=JST).isoformat()
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return datetime(d.year, d.month, d.day, tzinfo=JST).isoformat()
    return None


def resolve_created_at(source: str | None, metadata_raw: str | None) -> tuple[str, str]:
    """(iso, method) を返す。"""
    meta: dict = {}
    if metadata_raw:
        try:
            parsed = json.loads(metadata_raw)
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = {}

    for key, method in (("date", "meta.date"), ("diary_date", "meta.diary_date")):
        if key in meta and meta[key] is not None:
            iso = _normalize_to_jst_iso(str(meta[key]))
            if iso:
                return iso, method

    if source:
        m = re.search(r"(\d{8})", source)
        if m:
            iso = _normalize_to_jst_iso(m.group(1))
            if iso:
                return iso, "filename"

    return FALLBACK.isoformat(), "fallback_2025-12-01"


def plan_updates(conn: sqlite3.Connection) -> list[dict]:
    """一括投入日(2026-06-28)に潰れた行だけ直す。本番蒸留の本物の created_at は触らない。"""
    rows = conn.execute(
        "SELECT id, source, metadata, created_at FROM memories "
        "WHERE created_at LIKE '2026-06-28%'"
    ).fetchall()
    updates: list[dict] = []
    for memory_id, source, metadata, old in rows:
        new_iso, method = resolve_created_at(source, metadata)
        if new_iso != old:
            updates.append({
                "id": memory_id,
                "source": source,
                "method": method,
                "before": old,
                "after": new_iso,
            })
    return updates


def apply_updates(conn: sqlite3.Connection, updates: list[dict]) -> None:
    conn.executemany(
        "UPDATE memories SET created_at = ? WHERE id = ?",
        [(u["after"], u["id"]) for u in updates],
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="実際に UPDATE する")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[NG] DB が無い: {args.db}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(args.db))
    try:
        updates = plan_updates(conn)
        by_method: dict[str, int] = {}
        for u in updates:
            by_method[u["method"]] = by_method.get(u["method"], 0) + 1
        print(f"対象 {len(updates)} 件 / method={by_method}")
        if not args.apply:
            print("dry-run（--apply で本番）")
            for u in updates[:5]:
                print(f"  id={u['id']} {u['method']}: {u['before'][:19]} -> {u['after'][:19]}")
            return 0

        # 可逆性(B7): 一括UPDATEの前に必ず控えを取る（docstring頼みにしない）
        backup_path = backup_db(args.db)
        print(f"[backup] {backup_path}")

        apply_updates(conn, updates)
        CHANGE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "action": "created_at をメタ／ファイル名の実日付へ戻す",
            "when": datetime.now(tz=JST).isoformat(),
            "count": len(updates),
            "by_method": by_method,
            "fallback_rule": "継承記憶・出所なしは 2025-12-01T00:00:00+09:00",
            "samples": updates[:20],
        }
        CHANGE_REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] 更新 {len(updates)} 件 / レポート {CHANGE_REPORT_PATH}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
