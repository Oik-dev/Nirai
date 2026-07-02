"""統合品質のスポットチェック（読み取り専用・DBは変更しない）"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.embedder import OllamaEmbedder
from serina.memory.config import MigrateConfig
from serina.memory.db import DEFAULT_DB_PATH, get_connection
from serina.memory.store import MemoryStore
from serina.tools.migrate import DEFAULT_LEGACY_DIR, load_all_drafts

SAMPLE_N = 60


def preview(s: str, n: int = 100) -> str:
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def main() -> None:
    cfg = MigrateConfig()

    # --- 概要 ---
    conn = get_connection(DEFAULT_DB_PATH)
    total = conn.execute("SELECT COUNT(*) c FROM memories").fetchone()["c"]
    pinned = conn.execute("SELECT * FROM memories WHERE pinned=1").fetchall()
    types = conn.execute(
        "SELECT type, COUNT(*) c FROM memories GROUP BY type ORDER BY c DESC"
    ).fetchall()

    print("=== 概要 ===")
    print(f"記憶総数: {total}  固定記憶: {len(pinned)}")
    print("型の内訳:", {r["type"]: r["c"] for r in types})

    print("\n=== 固定記憶（全文・正しく固定されたか確認）===")
    for r in pinned:
        print(f"[id={r['id']}] importance={r['importance']} source={r['source']}")
        print(r["content"])
        print("-")

    # --- 複数ソースを束ねた記憶 ---
    print("\n=== 複数ソースを統合した記憶（merged_sources>=2、最大15件）===")
    rows = conn.execute("SELECT id, content, metadata FROM memories").fetchall()
    multi = []
    for r in rows:
        try:
            md = json.loads(r["metadata"] or "{}")
        except json.JSONDecodeError:
            md = {}
        ms = md.get("merged_sources") or []
        if len(ms) >= 2:
            multi.append((len(ms), r["id"], r["content"], ms))
    multi.sort(reverse=True)
    for cnt, mid, content, ms in multi[:15]:
        print(f"[id={mid}] sources={ms}")
        print("  ", preview(content, 120))
    print(f"(該当 {len(multi)} 件)")
    conn.close()

    # --- 統合判定の再現（サンプル）---
    # 旧ドラフトを最近傍とつき合わせ、本文が一致しない（=別文に畳まれた）ペアを表示。
    # これが「実際に統合された」候補。中身が本当に同一テーマかを目視する。
    print(f"\n=== 統合された候補ペア（サンプル{SAMPLE_N}件から抽出）===")
    print("旧=元ドラフト本文 / 既=統合先に残った本文。両者が別テーマなら過統合の疑い。")
    drafts, _ = load_all_drafts(DEFAULT_LEGACY_DIR, cfg)
    embedder = OllamaEmbedder()
    store = MemoryStore(embedder, db_path=DEFAULT_DB_PATH)

    random.seed(42)
    sample = random.sample(drafts, min(SAMPLE_N, len(drafts)))

    pairs = []
    for d in sample:
        emb = embedder.embed(d.content)
        sim = store.find_similar(emb, cfg.dedup_similarity_threshold, limit=1)
        if not sim:
            continue
        _, score, mem = sim[0]
        if " ".join(d.content.split()) == " ".join(mem["content"].split()):
            continue  # 自分自身（統合の勝者/新規）はスキップ
        pairs.append((score, d.content, mem["content"]))

    pairs.sort()  # 類似度の低い順＝あやしい順
    print(f"サンプル{len(sample)}件中、別文へ畳まれた候補: {len(pairs)}件")
    for score, dc, mc in pairs[:20]:
        flag = "⚠" if score < 0.92 else " "
        print(f"{flag} sim={score:.3f}")
        print(f"   旧: {preview(dc)}")
        print(f"   既: {preview(mc)}")


if __name__ == "__main__":
    main()
