#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""legacy 遺産を記憶DBへ投入する（記憶正本入れ直し）。

使い方:
    python tools/import_legacy_memories.py --db path/to.db
    python tools/import_legacy_memories.py --db path/to.db --dry-run

本番 wipe 後に呼ぶ。Chat.html は対象外。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.legacy_parse import (
    chunk_diary_body,
    parse_diary_file,
    parse_inherited_canon,
    parse_memory_json,
)
from serina.core.memory.store import MemoryStore, VECTOR_DIM_DEFAULT

LEGACY = ROOT / "legacy"
DEFAULT_DB = ROOT / "data" / "serina_memory.db"
DIARY_GLOB = "セリナの日記*.txt"


@dataclass
class ImportStats:
    diary_parents: int = 0
    diary_chunks: int = 0
    json_entries: int = 0
    inherited_cards: int = 0
    pinned: int = 0
    errors: list[str] = field(default_factory=list)


def import_legacy_into_store(store: MemoryStore, *, legacy_root: Path = LEGACY) -> ImportStats:
    """パース結果を store へ書き込む。"""
    stats = ImportStats()
    diary_dir = legacy_root / "記憶"
    for path in sorted(diary_dir.glob(DIARY_GLOB)):
        try:
            entries = parse_diary_file(path)
        except OSError as exc:
            stats.errors.append(f"{path.name}: {exc}")
            continue
        for entry in entries:
            parent_id = store.add_memory(
                entry.body,
                type="diary",
                importance=0.85,
                protection_grade="A",
                source=entry.source_label,
                created_at=entry.date_iso,
                embed=False,
                metadata_obj={"date": entry.date_iso[:10], "kind": "diary_parent"},
            )
            stats.diary_parents += 1
            for chunk in chunk_diary_body(entry.body):
                store.add_memory(
                    chunk,
                    type="event",
                    importance=0.6,
                    protection_grade="B",
                    source=entry.source_label,
                    parent_id=parent_id,
                    created_at=entry.date_iso,
                    embed=True,
                    metadata_obj={"date": entry.date_iso[:10], "kind": "diary_chunk"},
                )
                stats.diary_chunks += 1

    json_path = legacy_root / "セリナの記憶.json"
    if json_path.exists():
        for entry in parse_memory_json(json_path):
            store.add_memory(
                entry.body,
                type="knowledge",
                importance=0.7,
                protection_grade="B",
                source=entry.source_label,
                created_at=entry.date_iso,
                embed=True,
                metadata_obj={"date": entry.date_iso[:10], "kind": "json_subject"},
            )
            stats.json_entries += 1

    canon_path = legacy_root / "継承記憶r1.md"
    if canon_path.exists():
        for card in parse_inherited_canon(canon_path):
            store.add_memory(
                card.content,
                type=card.mem_type,
                importance=0.95 if card.pinned else 0.8,
                protection_grade=card.protection_grade,
                source=card.source_label,
                pinned=card.pinned,
                created_at="2025-03-01T00:00:00+09:00",
                embed=True,
                metadata_obj={"kind": "inherited_card"},
            )
            stats.inherited_cards += 1
            if card.pinned:
                stats.pinned += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="legacy 記憶を DB へ投入")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--legacy", type=Path, default=LEGACY)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--vector-dim", type=int, default=4, help="テスト用は4、本番は1024")
    parser.add_argument("--real-embed", action="store_true", help="Ollama bge-m3 で埋め込み")
    args = parser.parse_args()

    if args.dry_run:
        diary_n = 0
        for path in sorted((args.legacy / "記憶").glob(DIARY_GLOB)):
            diary_n += len(parse_diary_file(path))
        json_n = len(parse_memory_json(args.legacy / "セリナの記憶.json")) if (args.legacy / "セリナの記憶.json").exists() else 0
        inh_n = len(parse_inherited_canon(args.legacy / "継承記憶r1.md")) if (args.legacy / "継承記憶r1.md").exists() else 0
        print(json.dumps({"diary_entries": diary_n, "json": json_n, "inherited": inh_n}, ensure_ascii=False, indent=2))
        return 0

    if args.real_embed:
        embedder = OllamaEmbedder()
        dim = VECTOR_DIM_DEFAULT
    else:
        embedder = OllamaEmbedder(call_fn=lambda model, text: [1.0] + [0.0] * (args.vector_dim - 1))
        dim = args.vector_dim

    store = MemoryStore(str(args.db), embedder=embedder, vector_dim=dim)
    stats = import_legacy_into_store(store, legacy_root=args.legacy)
    print(
        json.dumps(
            {
                "diary_parents": stats.diary_parents,
                "diary_chunks": stats.diary_chunks,
                "json_entries": stats.json_entries,
                "inherited_cards": stats.inherited_cards,
                "pinned": stats.pinned,
                "errors": stats.errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
