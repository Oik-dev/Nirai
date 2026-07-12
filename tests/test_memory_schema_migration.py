"""記憶DBスキーマ移行のテスト。設計書v2 §4.2, §4.6

既存memoriesテーブルに 機微等級・化粧版・保護等級 を追加する。安全側初期値（機微2固定）で開始。
"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.tools.migrate_memory_schema import assign_initial_protection_grades, migrate


def _make_legacy_db() -> Path:
    tmp = Path(tempfile.mkdtemp()) / "legacy.db"
    conn = sqlite3.connect(tmp)
    conn.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 0.5,
            pinned INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_accessed TEXT NOT NULL,
            access_count INTEGER NOT NULL DEFAULT 0,
            source TEXT,
            parent_id INTEGER,
            metadata TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO memories (type, content, pinned, created_at, last_accessed) VALUES (?, ?, ?, ?, ?)",
        ("fact", "テスト記憶", 0, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.execute(
        "INSERT INTO memories (type, content, pinned, created_at, last_accessed) VALUES (?, ?, ?, ?, ?)",
        ("knowledge", "正典由来の記憶", 1, "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()
    return tmp


def test_migrate_adds_new_columns_with_safe_defaults() -> None:
    db_path = _make_legacy_db()
    migrate(db_path)

    conn = sqlite3.connect(db_path)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    assert "sensitivity_grade" in cols
    assert "cosmetic_version" in cols
    assert "protection_grade" in cols
    assert "sensitivity_assessed" in cols

    row = conn.execute(
        "SELECT sensitivity_grade, cosmetic_version, protection_grade, sensitivity_assessed FROM memories"
    ).fetchone()
    assert row[0] == 2, "既存記憶の機微等級は安全側の2で初期化されるべき（§4.6-2）"
    assert row[1] is None
    assert row[2] == "B"
    assert row[3] == 0, "既存記憶は未査定(0)で初期化されるべき（§4.6-3）"
    conn.close()


def test_migrate_is_idempotent() -> None:
    db_path = _make_legacy_db()
    migrate(db_path)
    migrate(db_path)  # 2回実行してもエラーにならない

    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(memories)")]
    assert cols.count("sensitivity_grade") == 1
    assert cols.count("sensitivity_assessed") == 1
    conn.close()


def test_assign_initial_protection_grades_sets_pinned_to_s() -> None:
    """§4.6-4: 正典由来の固定9件(pinned=1)は保護等級S、他はB（既定値のまま）"""
    db_path = _make_legacy_db()
    migrate(db_path)
    assign_initial_protection_grades(db_path)

    conn = sqlite3.connect(db_path)
    rows = {row[0]: row[1] for row in conn.execute("SELECT pinned, protection_grade FROM memories")}
    conn.close()

    assert rows[1] == "S", "正典由来(pinned=1)は保護等級Sになるべき"
    assert rows[0] == "B", "非正典(pinned=0)は保護等級Bのまま（マスター判断により全件Bで開始）"


def main() -> None:
    tests = [
        test_migrate_adds_new_columns_with_safe_defaults,
        test_migrate_is_idempotent,
        test_assign_initial_protection_grades_sets_pinned_to_s,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
