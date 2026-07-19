# -*- coding: utf-8 -*-
"""DB → life/ 一方向 md 生成（合意台帳 §1 議題1 / Wave 4 A10）。

手動編集の反映経路は持たない。次回生成で上書きされる。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.summaries import load_summary_blocks  # noqa: E402
from serina.core.memory.facts import ensure_facts_schema  # noqa: E402

DEFAULT_DB_PATH = ROOT / "data" / "serina_memory.db"
DEFAULT_LIFE_DIR = ROOT / "life"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def export_life(
    db_path: Path | str,
    output_dir: Path | str,
    *,
    summaries_path: Path | str | None = None,
) -> list[Path]:
    """DB と要約ブロックから life/ 配下の md を生成する（上書きのみ）。"""
    src = Path(db_path)
    dest_root = Path(output_dir)
    dest_root.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(src))
    conn.row_factory = sqlite3.Row
    try:
        ensure_facts_schema(conn)
        written: list[Path] = []

        index_path = dest_root / "index.md"
        mem_count = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()["c"]
        fact_count = conn.execute(
            "SELECT COUNT(*) AS c FROM facts WHERE status = 'active'"
        ).fetchone()["c"]
        index_path.write_text(
            "\n".join(
                [
                    "# Serina Life View",
                    "",
                    f"生成時刻: {_utc_now_iso()}",
                    f"記憶件数: {mem_count}",
                    f"有効 fact 件数: {fact_count}",
                    "",
                    "※ このディレクトリは DB から一方向生成される人間用ビューです。",
                    "手動編集は次回生成で上書きされます。",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        written.append(index_path)

        memories_path = dest_root / "memories.md"
        rows = conn.execute(
            "SELECT id, type, content, protection_grade, created_at "
            "FROM memories ORDER BY id DESC LIMIT 200"
        ).fetchall()
        mem_lines = ["# 記憶（直近200件）", ""]
        for row in rows:
            preview = row["content"].replace("\n", " ")[:200]
            mem_lines.append(
                f"- #{row['id']} [{row['type']}] ({row['protection_grade']}) "
                f"{row['created_at'][:10]}: {preview}"
            )
        memories_path.write_text("\n".join(mem_lines) + "\n", encoding="utf-8")
        written.append(memories_path)

        facts_path = dest_root / "facts.md"
        fact_rows = conn.execute(
            "SELECT id, statement, valid_from, valid_to, status "
            "FROM facts WHERE status = 'active' ORDER BY recorded_at DESC LIMIT 200"
        ).fetchall()
        fact_lines = ["# 時間付き事実（active）", ""]
        for row in fact_rows:
            valid = row["valid_from"][:10]
            if row["valid_to"]:
                valid += f" 〜 {row['valid_to'][:10]}"
            fact_lines.append(f"- {row['id']}: {row['statement']} （{valid}）")
        facts_path.write_text("\n".join(fact_lines) + "\n", encoding="utf-8")
        written.append(facts_path)

        summaries = load_summary_blocks(summaries_path)
        summaries_path_out = dest_root / "summaries.md"
        summaries_path_out.write_text(
            "\n".join(
                [
                    "# 常駐要約ブロック",
                    "",
                    "## 好み（prefs_summary）",
                    "",
                    summaries.prefs_summary or "（未設定）",
                    "",
                    "## 関係（relation_summary）",
                    "",
                    summaries.relation_summary or "（未設定）",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        written.append(summaries_path_out)

        return written
    finally:
        conn.close()


def main() -> int:
    try:
        paths = export_life(DEFAULT_DB_PATH, DEFAULT_LIFE_DIR)
    except FileNotFoundError as exc:
        print(f"[NG] {exc}")
        return 1
    print(f"[OK] life/ に {len(paths)} ファイルを生成")
    for path in paths:
        print(f"  - {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
