# -*- coding: utf-8 -*-
"""memoriesテーブルのtype列をepisodic/semanticの2値へ統合する（2026-07-23設計改訂）。

対応:
  diary                                  -> episodic
  event / knowledge / promise / relationship -> semantic
                                             （元のtype値をmetadata.legacy_typeへ焼き込む。
                                              移行検証・legacy_type検索用。旧Pulse約束一覧は
                                              予定窓方式へ置換済み）
  fact                                    -> semantic
                                             （本番蒸留の現行生成分。legacy_typeは付けない）

promise行のprotection_grade列は変更しない（等級は保持されたまま、type列だけ変わる）。

使い方:
  python tools/migrate_memory_types.py          # dry-run（対象件数の表示のみ）
  python tools/migrate_memory_types.py --apply   # 本番適用（backup_db を自動実行してから UPDATE）
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from mind.core.soul import DATA_DIR  # noqa: E402
from tools.backup_db import backup_db  # noqa: E402

DB_PATH = DATA_DIR / "serina_memory.db"
JST = ZoneInfo("Asia/Tokyo")
CHANGE_REPORT_PATH = DATA_DIR / "change_reports" / "migrate_memory_types.json"

DIARY_TO_EPISODIC = "diary"
LEGACY_TO_SEMANTIC = ("event", "knowledge", "promise", "relationship")
FACT_TO_SEMANTIC = "fact"
KNOWN_TYPES = frozenset({DIARY_TO_EPISODIC, *LEGACY_TO_SEMANTIC, FACT_TO_SEMANTIC, "episodic", "semantic"})


def _merge_legacy_type(metadata_raw: str | None, legacy_type: str) -> str:
    meta: dict = {}
    if metadata_raw:
        try:
            parsed = json.loads(metadata_raw)
            if isinstance(parsed, dict):
                meta = parsed
        except json.JSONDecodeError:
            meta = {}
    meta["legacy_type"] = legacy_type
    return json.dumps(meta, ensure_ascii=False)


def plan_updates(conn: sqlite3.Connection) -> dict:
    """typeごとの対象件数と実UPDATE内容を組み立てる。未知のtype値は対象外で報告のみ。"""
    rows = conn.execute("SELECT id, type, metadata FROM memories").fetchall()
    by_type: dict[str, int] = {}
    unknown_types: dict[str, int] = {}
    diary_ids: list[int] = []
    fact_ids: list[int] = []
    legacy_updates: list[tuple[int, str, str]] = []  # (id, new_metadata, legacy_type)

    for memory_id, mem_type, metadata in rows:
        by_type[mem_type] = by_type.get(mem_type, 0) + 1
        if mem_type == DIARY_TO_EPISODIC:
            diary_ids.append(memory_id)
        elif mem_type == FACT_TO_SEMANTIC:
            fact_ids.append(memory_id)
        elif mem_type in LEGACY_TO_SEMANTIC:
            new_metadata = _merge_legacy_type(metadata, mem_type)
            legacy_updates.append((memory_id, new_metadata, mem_type))
        elif mem_type not in KNOWN_TYPES:
            unknown_types[mem_type] = unknown_types.get(mem_type, 0) + 1

    return {
        "by_type_before": by_type,
        "diary_ids": diary_ids,
        "fact_ids": fact_ids,
        "legacy_updates": legacy_updates,
        "unknown_types": unknown_types,
    }


def apply_updates(conn: sqlite3.Connection, plan: dict) -> None:
    if plan["diary_ids"]:
        conn.executemany(
            "UPDATE memories SET type = 'episodic' WHERE id = ?",
            [(i,) for i in plan["diary_ids"]],
        )
    if plan["fact_ids"]:
        conn.executemany(
            "UPDATE memories SET type = 'semantic' WHERE id = ?",
            [(i,) for i in plan["fact_ids"]],
        )
    if plan["legacy_updates"]:
        conn.executemany(
            "UPDATE memories SET type = 'semantic', metadata = ? WHERE id = ?",
            [(new_meta, mid) for mid, new_meta, _legacy in plan["legacy_updates"]],
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
        plan = plan_updates(conn)
        legacy_by_type: dict[str, int] = {}
        for _mid, _meta, legacy_type in plan["legacy_updates"]:
            legacy_by_type[legacy_type] = legacy_by_type.get(legacy_type, 0) + 1

        print(f"移行前の分布: {plan['by_type_before']}")
        print(f"diary -> episodic: {len(plan['diary_ids'])}件")
        print(f"fact -> semantic (legacy_typeなし): {len(plan['fact_ids'])}件")
        print(f"legacy(event/knowledge/promise/relationship) -> semantic: {len(plan['legacy_updates'])}件 / 内訳={legacy_by_type}")
        if plan["unknown_types"]:
            print(f"[注意] 未知のtype値のため対象外: {plan['unknown_types']}", file=sys.stderr)

        if not args.apply:
            print("dry-run（--apply で本番適用。本番DBへの適用は必ずマスターへ確認してから実行すること）")
            return 0

        backup_path = backup_db(args.db)
        print(f"[backup] {backup_path}")

        apply_updates(conn, plan)

        after_rows = conn.execute(
            "SELECT type, COUNT(*) FROM memories GROUP BY type",
        ).fetchall()
        by_type_after = {t: c for t, c in after_rows}
        print(f"移行後の分布: {by_type_after}")

        CHANGE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "action": "memoriesテーブルのtype列をepisodic/semanticの2値へ統合",
            "when": datetime.now(tz=JST).isoformat(),
            "by_type_before": plan["by_type_before"],
            "by_type_after": by_type_after,
            "diary_to_episodic_count": len(plan["diary_ids"]),
            "fact_to_semantic_count": len(plan["fact_ids"]),
            "legacy_to_semantic_count": len(plan["legacy_updates"]),
            "legacy_to_semantic_by_original_type": legacy_by_type,
            "unknown_types_skipped": plan["unknown_types"],
            "legacy_type_tagging": "event/knowledge/promise/relationshipはmetadata.legacy_typeへ元のtype値を保存",
        }
        CHANGE_REPORT_PATH.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[OK] 移行完了 / レポート {CHANGE_REPORT_PATH}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
