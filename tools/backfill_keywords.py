"""正典（pinned）記憶への trigger_keywords バックフィル（3c 前提穴埋め・Ollama必須）

移行v2由来の記憶は trigger_keywords を持たないため、pinned特例の撤去後に
トリガー想起が発火しない。理性エンジンでキーワードを生成して metadata に付与する。
- 既に trigger_keywords を持つ記憶はスキップ（冪等）
- content には触れない（再埋め込みは走らない・正典保護）
- 結果は日本語レポートで必ず印字（無言破棄禁止）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# cp932コンソールで絵文字入りの記憶本文が印字クラッシュしないように（3a知見の踏襲）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.chat_llm import OllamaChatConnector
from serina.connectors.embedder import OllamaEmbedder
from serina.core.config import CoreConfig
from serina.core.reflection_parser import extract_block
from serina.memory.db import DEFAULT_DB_PATH
from serina.memory.store import MemoryStore

KEYWORD_PROMPT = """以下の「記憶」に対して、会話中にこの記憶を思い出すべき引き金となるキーワードを3〜8語生成してください。
- 別名・略称・表記ゆれ・言い換えをすべて含めること（例: 宮古島なら「宮古島,高野漁港,約束の海,ビーチ,再会」）
- 出力は <keywords>語1,語2,語3</keywords> の形式のみ。前置き・解説は禁止。

【記憶】
{content}"""


def run_backfill(
    store: MemoryStore,
    connector,
    targets: list[dict],
    dry_run: bool = False,
    temperature: float = 0.2,
) -> dict[str, int]:
    """対象記憶へキーワードを生成・付与。冪等（付与済みはスキップ）。集計を返す。"""
    stats = {"updated": 0, "skipped": 0, "failed": 0}
    for mem in targets:
        meta = mem.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        if meta.get("trigger_keywords"):
            stats["skipped"] += 1
            print(f"  #{mem['id']} スキップ（付与済み）: {mem['content'][:40]}…")
            continue
        raw = connector.chat(
            "あなたは正確なキーワード抽出器です。",
            [{"role": "user", "content": KEYWORD_PROMPT.format(content=mem["content"][:1500])}],
            options={"temperature": temperature, "num_ctx": 4096},
        )
        block = extract_block("keywords", raw)
        keywords = [k.strip() for k in (block or "").replace("、", ",").split(",") if k.strip()]
        if not keywords:
            stats["failed"] += 1
            print(f"  #{mem['id']} 生成失敗（keywordsタグなし）: {raw[:80]}…")
            continue
        print(f"  #{mem['id']} {mem['content'][:40]}…")
        print(f"     → {', '.join(keywords)}")
        if not dry_run:
            meta["trigger_keywords"] = keywords
            store.update(mem["id"], metadata=meta)
            stats["updated"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="trigger_keywords バックフィル")
    parser.add_argument("--dry-run", action="store_true", help="生成のみ・書き込まない")
    parser.add_argument("--all-facts", action="store_true",
                        help="pinnedに加え type=fact/knowledge 全件も対象にする")
    parser.add_argument("--model", default=CoreConfig().reason_model)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    cfg = CoreConfig()
    OllamaChatConnector.ensure_model_available(args.model, cfg.base_url)
    connector = OllamaChatConnector(args.model, cfg.base_url, keep_alive=0)
    store = MemoryStore(OllamaEmbedder(base_url=cfg.base_url), db_path=args.db)

    targets = list(store.get_all_pinned())
    if args.all_facts:
        seen = {m["id"] for m in targets}
        for t in ("fact", "knowledge"):
            targets.extend(m for m in store.list_memories_by_type(t) if m["id"] not in seen)

    print(f"対象: {len(targets)} 件（{'dry-run' if args.dry_run else '本実行'}）")
    stats = run_backfill(store, connector, targets, args.dry_run, cfg.reason_temperature)
    print(f"\n完了: 付与 {stats['updated']} 件 / スキップ {stats['skipped']} 件 / 失敗 {stats['failed']} 件")
    if args.dry_run:
        print("（dry-run のため書き込みはしていません。--dry-run を外して本実行してください）")


if __name__ == "__main__":
    main()
