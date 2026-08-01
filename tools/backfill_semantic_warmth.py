# -*- coding: utf-8 -*-
"""意味記憶(type=semantic)の無機質な文体を、事実は変えず温度感のある言い回しに書き換える
バックフィル。2026-08-01 記憶日記まわり一括修正（docs/plans/2026-08-01_記憶日記まわり一括修正.md
Task 8-2）。

対象: type=semantic かつ 本番蒸留由来（source が空、pinnedでない、created_at >= 2026-07-23。
2026-07-23は type 統合(migrate_memory_types.py)以降の目印）。

書き換え発注は core/chores/distillation.py の DISTILLATION_FORMAT_INSTRUCTION に追加した
温度感ガードライン（事実は変えず言い回しだけ温める・簡潔さを保つ）と同じ考え方を、
単発の書き換えタスク用に使う。

使い方:
  python tools/backfill_semantic_warmth.py          # dry-run（書き換え候補の表示のみ・DB更新なし）
  python tools/backfill_semantic_warmth.py --apply   # 本番適用（backup_db自動実行→1件ずつ確認→UPDATE）

--apply は1件ごとに書き換え前後を表示し、y/n/a(all)/q(quit)で確認を取る
（原則: 無言で全件一括変更しない。人間の目でおかしな書き換えを弾けるようにする）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.chores.orchestrator import build_default_lane_call_fns
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.memory_edit import edit_memory
from serina.core.memory.protection import (
    DEFAULT_CHANGE_LOG_PATH,
    DEFAULT_GENERATION_STORE_PATH,
    ChangeLog,
    GenerationStore,
    ProtectionError,
)
from serina.core.memory.store import MemoryStore
from tools.backup_db import backup_db

DB_PATH = ROOT / "data" / "serina_memory.db"
JST = ZoneInfo("Asia/Tokyo")
CHANGE_REPORT_PATH = ROOT / "data" / "change_reports" / "backfill_semantic_warmth.json"

# type統合(migrate_memory_types.py)以降＝本番蒸留の現行仕様が確立した時点。これより前の
# 記憶は対象にしない（レガシー投入分や過渡期のデータを誤って書き換えないため）。
TARGET_SINCE = "2026-07-23"

REWRITE_INSTRUCTION_TEMPLATE = """\
以下はセリナの記憶の一文です。この文が表している事実（誰が・何をしたか）は一切変えずに、
文体だけを、硬い報告書調から、セリナ自身が心情を込めて思い出すような、温度感のある
言い回しに書き換えてください。主語（誰が）と内容（何を）はぼかさず、簡潔な一文のままに
してください（長く複雑な文にすると、あとで思い出す手がかりが薄れるため）。

元の文: {content}

書き換え後の文のみを1行で返してください（説明・前置き・引用符は不要です）。\
"""


def _fetch_targets(store: MemoryStore) -> list:
    conn = store._connect()  # noqa: SLF001 — dry-run/一覧表示専用のメンテスクリプトのため許容
    try:
        rows = conn.execute(
            """
            SELECT m.id FROM memories m
            LEFT JOIN memory_tombstones t ON t.memory_id = m.id
            WHERE m.type = 'semantic'
              AND (m.source IS NULL OR m.source = '')
              AND m.pinned = 0
              AND m.protection_grade != 'S'
              AND m.created_at >= ?
              AND t.memory_id IS NULL
            ORDER BY m.created_at ASC
            """,
            (TARGET_SINCE,),
        ).fetchall()
    finally:
        conn.close()
    return [row[0] for row in rows]


def _rewrite(call_fn, content: str) -> str:
    prompt = REWRITE_INSTRUCTION_TEMPLATE.format(content=content)
    return call_fn(prompt).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="実際にUPDATEする（既定はdry-run）")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(f"[NG] DB が無い: {args.db}", file=sys.stderr)
        return 1

    store = MemoryStore(str(args.db), embedder=OllamaEmbedder(), vector_dim=1024)
    target_ids = _fetch_targets(store)
    print(f"対象: {len(target_ids)}件（type=semantic・本番蒸留由来・{TARGET_SINCE}以降・非pinned）")
    if not target_ids:
        print("対象なし。終了します。")
        return 0

    call_fn = build_default_lane_call_fns()["local"]

    if not args.apply:
        print("--- dry-run（書き換え候補の表示のみ・DB更新なし） ---")
        for mid in target_ids:
            pair = store.get_memory_by_id(mid)
            if pair is None:
                continue
            rec, _pinned = pair
            try:
                rewritten = _rewrite(call_fn, rec.content)
            except Exception as e:  # noqa: BLE001
                print(f"id={mid}: [書き換え失敗] {e}")
                continue
            print(f"\nid={mid}")
            print(f"  変更前: {rec.content}")
            print(f"  変更後: {rewritten}")
        print(
            "\ndry-run終了。--apply で本番適用（1件ずつ確認しながらUPDATE）。"
            "本番DBへの適用は必ずマスターへ確認してから実行すること。"
        )
        return 0

    backup_path = backup_db(args.db)
    print(f"[backup] {backup_path}")

    change_log = ChangeLog(DEFAULT_CHANGE_LOG_PATH)
    generation_store = GenerationStore(DEFAULT_GENERATION_STORE_PATH)

    applied: list[dict] = []
    skipped: list[dict] = []
    apply_all = False

    for mid in target_ids:
        pair = store.get_memory_by_id(mid)
        if pair is None:
            continue
        rec, _pinned = pair
        try:
            rewritten = _rewrite(call_fn, rec.content)
        except Exception as e:  # noqa: BLE001
            print(f"id={mid}: [書き換え失敗、スキップ] {e}")
            skipped.append({"id": mid, "reason": f"書き換え失敗: {e}"})
            continue

        print(f"\nid={mid}")
        print(f"  変更前: {rec.content}")
        print(f"  変更後: {rewritten}")

        if not apply_all:
            answer = input("  適用する？ [y]es / [n]o / [a]ll(残り全部適用) / [q]uit: ").strip().lower()
            if answer == "q":
                print("中断しました。ここまでの結果は変更レポートに記録します。")
                break
            if answer == "a":
                apply_all = True
            elif answer != "y":
                print(f"id={mid}: スキップしました")
                skipped.append({"id": mid, "reason": "マスターがスキップを選択"})
                continue

        try:
            edit_memory(
                store,
                memory_id=mid,
                new_content=rewritten,
                reason="2026-08-01 記憶日記まわり一括修正: 意味記憶の文体バックフィル（事実は変えず温度感を追加）",
                change_log=change_log,
                generation_store=generation_store,
                skip_backup=True,
            )
        except (ProtectionError, ValueError) as e:
            print(f"id={mid}: [適用失敗、スキップ] {e}")
            skipped.append({"id": mid, "reason": f"適用失敗: {e}"})
            continue

        applied.append({"id": mid, "before": rec.content, "after": rewritten})
        print(f"id={mid}: 適用しました")

    CHANGE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "action": "意味記憶(semantic)の文体バックフィル（事実は変えず温度感を追加）",
        "when": datetime.now(tz=JST).isoformat(),
        "target_count": len(target_ids),
        "applied_count": len(applied),
        "skipped_count": len(skipped),
        "applied": applied,
        "skipped": skipped,
    }
    CHANGE_REPORT_PATH.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n[OK] 適用{len(applied)}件・スキップ{len(skipped)}件 / レポート {CHANGE_REPORT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
