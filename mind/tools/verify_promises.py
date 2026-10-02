"""約束・正典の健全性検証（読み取り専用）"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

import sqlite3

from mind.core.idea import DATA_DIR  # noqa: E402

DEFAULT_DB_PATH = DATA_DIR / "serina_memory.db"

REQUIRED_KEYWORDS = (
    "宮古島",
    "高野漁港",
    "約束の海",
    "与那覇",
    "Roca",
    "絶対に破らない",
)
GARBAGE_PATTERN = re.compile(r"^[-*]?\s*###\s+\S+\s*$")


def main() -> None:
    conn = sqlite3.connect(DEFAULT_DB_PATH)
    conn.row_factory = sqlite3.Row
    pinned = conn.execute("SELECT * FROM memories WHERE pinned=1 ORDER BY id").fetchall()
    all_rows = conn.execute("SELECT id, content, metadata, source FROM memories").fetchall()
    conn.close()

    print("=== 8-1 約束の健全性 ===")
    print(f"pinned 記憶数: {len(pinned)}")

    print("\n--- pinned 一覧（全文） ---")
    for row in pinned:
        md = {}
        try:
            md = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            pass
        merged = md.get("merged_sources") or []
        print(f"[id={row['id']}] source={row['source']} merged_sources={merged}")
        print(row["content"])
        print("-")

    # ゴミ片チェック
    garbage = []
    for row in all_rows:
        content = (row["content"] or "").strip()
        if GARBAGE_PATTERN.match(content):
            garbage.append(row["id"])
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        if len(lines) == 2 and lines[0] and lines[1].startswith("###"):
            if lines[1] != lines[0]:
                garbage.append(row["id"])

    print(f"\nゴミ片（- ### のみ等）: {len(set(garbage))} 件 {sorted(set(garbage)) or 'なし'}")

    # 正典キーワード（継承記憶由来 pinned）
    canonical_pinned = [r for r in pinned if r["source"] == "継承記憶r1.md"]
    combined = "\n".join(r["content"] for r in canonical_pinned)
    print("\n正典キーワード（pinned・継承記憶由来）:")
    for kw in REQUIRED_KEYWORDS:
        ok = kw in combined
        print(f"  {kw}: {'OK' if ok else 'NG'}")

    # canonical 汚染チェック
    polluted = []
    for row in pinned:
        if row["source"] != "継承記憶r1.md":
            continue
        try:
            md = json.loads(row["metadata"] or "{}")
        except json.JSONDecodeError:
            md = {}
        merged = md.get("merged_sources") or []
        bad = [s for s in merged if s != "継承記憶r1.md"]
        if bad:
            polluted.append((row["id"], bad))

    print(f"\n正典 pinned の merged_sources 汚染: {len(polluted)} 件")
    for mid, bad in polluted:
        print(f"  id={mid} 混入={bad}")

    has_priority = any("最優先" in r["content"] for r in pinned)
    has_promise_section = any("約束" in r["content"] or "蘇らせ" in r["content"] for r in pinned)
    print(f"\n最優先ルールを含む pinned: {'OK' if has_priority else 'NG'}")
    print(f"約束系内容を含む pinned: {'OK' if has_promise_section else 'NG'}")


if __name__ == "__main__":
    main()
