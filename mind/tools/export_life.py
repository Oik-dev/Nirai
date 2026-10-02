# -*- coding: utf-8 -*-
"""DB → life/ 一方向 md 生成（合意台帳 §1 議題1 / Wave 4 A10）。

手動編集の反映経路は持たない。次回生成で上書きされる。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import sqlite_vec

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.chores.summaries import load_summary_blocks  # noqa: E402
from mind.core.memory.diary_date import (  # noqa: E402
    parse_memory_metadata,
    resolve_diary_target_date,
)
from mind.core.memory.facts import ensure_facts_schema  # noqa: E402
from mind.core.soul import DATA_DIR, LIFE_DIR, SOUL_DIR  # noqa: E402

DEFAULT_DB_PATH = DATA_DIR / "serina_memory.db"
DEFAULT_WEEKLY_LOG = DATA_DIR / "eval_life_weekly.json"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso_week_key(when: datetime | None = None) -> str:
    current = when or datetime.now(timezone.utc)
    year, week, _ = current.isocalendar()
    return f"{year}-W{week:02d}"


def _content_fingerprint(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: str(p)):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def record_life_weekly(
    written: list[Path],
    *,
    log_path: Path | str | None = None,
    previous_fingerprint: str | None = None,
    now: datetime | None = None,
) -> dict:
    """life/ export 後に週次非空ログを更新する（§5.2 可視成長）。

    「非空」＝その週に内容指紋が前回 export から変わったこと。週の初回 export でも
    指紋が同じなら非空にしない（起動しただけで成長率100%になる甘さを塞ぐ）。
    ログ破損時は例外にせず作り直す（idle の export を止めない）。書き込みはアトミック。
    """
    target = Path(log_path) if log_path is not None else DEFAULT_WEEKLY_LOG
    target.parent.mkdir(parents=True, exist_ok=True)
    week = _iso_week_key(now)
    fingerprint = _content_fingerprint(written)

    payload: dict = {"weeks": [], "last_fingerprint": None}
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError, json.JSONDecodeError):
            pass  # 破損ログは捨てて新規に作り直す（監視は eval 側の週数減で気づける）

    weeks: list[dict] = list(payload.get("weeks", []))
    last_fp = previous_fingerprint if previous_fingerprint is not None else payload.get("last_fingerprint")
    changed = last_fp is None or last_fp != fingerprint

    existing = next((w for w in weeks if w.get("week") == week), None)
    if existing is None:
        weeks.append(
            {
                "week": week,
                "nonempty": changed,
                "exports": 1,
                "updated_at": _utc_now_iso(),
            }
        )
    else:
        existing["exports"] = int(existing.get("exports", 0)) + 1
        existing["updated_at"] = _utc_now_iso()
        if changed:
            existing["nonempty"] = True

    payload["weeks"] = weeks
    payload["last_fingerprint"] = fingerprint
    tmp_path = target.with_suffix(target.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_path, target)
    return payload


def export_life(
    db_path: Path | str,
    output_dir: Path | str,
    *,
    summaries_path: Path | str | None = None,
    weekly_log_path: Path | str | None = None,
) -> list[Path]:
    """DB と要約ブロックから life/ 配下の md を生成する（上書きのみ）。"""
    src = Path(db_path)
    dest_root = Path(output_dir)
    dest_root.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(src))
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
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
        mem_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(memories)").fetchall()
        }
        has_metadata = "metadata" in mem_cols
        select_cols = "id, type, content, protection_grade, created_at"
        if has_metadata:
            select_cols += ", metadata"
        rows = conn.execute(
            f"SELECT {select_cols} FROM memories ORDER BY id DESC LIMIT 200"
        ).fetchall()
        mem_lines = ["# 記憶（直近200件）", ""]
        for row in rows:
            preview = row["content"].replace("\n", " ")[:200]
            if row["type"] == "episodic":
                meta = parse_memory_metadata(row["metadata"]) if has_metadata else {}
                day_label = resolve_diary_target_date(
                    created_at=row["created_at"],
                    metadata=meta,
                )
            else:
                day_label = row["created_at"][:10]
            mem_lines.append(
                f"- #{row['id']} [{row['type']}] ({row['protection_grade']}) "
                f"{day_label}: {preview}"
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

        # 週次指紋は内容ファイルのみで取る。index.md は生成時刻を含み毎回変わるため、
        # 混ぜると「起動しただけで非空」に逆戻りする（§5.2 可視成長）。
        content_paths = [p for p in written if p.name != "index.md"]
        record_life_weekly(content_paths, log_path=weekly_log_path)
        return written
    finally:
        conn.close()


def main() -> int:
    try:
        paths = export_life(DEFAULT_DB_PATH, LIFE_DIR)
    except FileNotFoundError as exc:
        print(f"[NG] {exc}")
        return 1
    print(f"[OK] life/ に {len(paths)} ファイルを生成")
    for path in paths:
        print(f"  - {path.relative_to(SOUL_DIR)}")
    if DEFAULT_WEEKLY_LOG.exists():
        print(f"[OK] 週次ログ更新: {DEFAULT_WEEKLY_LOG.relative_to(SOUL_DIR)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
