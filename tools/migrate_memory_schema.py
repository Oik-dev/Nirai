"""記憶DBスキーマ移行: 機微等級・化粧版・保護等級を追加。設計書v2 §4.2, §4.6

破壊的操作の前に必ず tools/backup_db.py を実行すること（設計書v2 §5.5-5）。
このスクリプト自体は列追加のみで、既存データを書き換えない（安全側デフォルト値で追加するのみ）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "serina_memory.db"

# (列名, 型と制約) — 既存858件は安全側「機微2＝ローカルのみ」で初期化する（§4.6-2）
NEW_COLUMNS = [
    ("sensitivity_grade", "INTEGER NOT NULL DEFAULT 2"),
    ("cosmetic_version", "TEXT"),
    ("protection_grade", "TEXT NOT NULL DEFAULT 'B'"),
    # §4.6-3: 未査定(0)/査定済み(1)の区別。全件既定sensitivity_grade=2だけでは
    # 「安全側で止めている未査定」と「査定の結果2だった」が区別できないため必要。
    ("sensitivity_assessed", "INTEGER NOT NULL DEFAULT 0"),
]


def migrate(db_path: Path | str | None = None) -> list[str]:
    """memoriesテーブルへ新しい列を追加する。既に存在する列はスキップする（冪等）。

    戻り値: 実際に追加した列名のリスト
    """
    target = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(target)
    try:
        existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
        added: list[str] = []
        for col_name, col_def in NEW_COLUMNS:
            if col_name in existing_cols:
                continue
            conn.execute(f"ALTER TABLE memories ADD COLUMN {col_name} {col_def}")
            added.append(col_name)
        conn.commit()
        return added
    finally:
        conn.close()


def assign_initial_protection_grades(db_path: Path | str | None = None) -> int:
    """正典由来の固定9件（pinned=1）に保護等級Sを付与する（§4.6-4）。

    非正典の既存記憶は移行時の既定値B（migrate()のデフォルト）のまま据え置く
    （マスター判断: 全件B開始。個別のA昇格は今後の会話・裏方便で行う）。
    戻り値: Sへ更新した件数
    """
    target = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(target)
    try:
        cursor = conn.execute("UPDATE memories SET protection_grade = 'S' WHERE pinned = 1")
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


if __name__ == "__main__":
    result = migrate()
    if result:
        print(f"追加した列: {', '.join(result)}")
    else:
        print("追加すべき列はなかった（既に移行済み）")

    s_count = assign_initial_protection_grades()
    print(f"保護等級Sへ更新した件数: {s_count}")
