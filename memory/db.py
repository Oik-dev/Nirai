"""SQLite + sqlite-vec による Memory DB の初期化と接続"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlite_vec

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "serina_memory.db"

SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS profile (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS memories (
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
        metadata TEXT,
        FOREIGN KEY (parent_id) REFERENCES memories(id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type)",
    "CREATE INDEX IF NOT EXISTS idx_memories_pinned ON memories(pinned)",
    "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance)",
    """
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_history_session_ts ON history(session_id, ts)",
    """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        last_activity TEXT NOT NULL,
        distilled_at TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status)",
    """
    CREATE TABLE IF NOT EXISTS archived_history (
        id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        ts TEXT NOT NULL,
        archived_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_archived_session ON archived_history(session_id)",
    """
    CREATE TABLE IF NOT EXISTS open_threads (
        id INTEGER PRIMARY KEY,
        question TEXT NOT NULL,
        context TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        source_session TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_open_threads_status ON open_threads(status)",
    # open中の同一質問はDBレベルでも一意（add_open_thread の事前チェックの保険）
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_open_threads_open_question "
    "ON open_threads(question) WHERE status = 'open'",
    """
    CREATE TABLE IF NOT EXISTS state_audit (
        id INTEGER PRIMARY KEY,
        session_id TEXT NOT NULL,
        param TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        reason TEXT,
        ts TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_state_audit_session ON state_audit(session_id)",
]


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """sqlite-vec をロードした接続を返す"""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 3b: 対話スレッドと蒸留スレッドが並行書き込みするため、ロック競合時は待ち、
    # WAL で「書き込み中も読める」ようにする（quality-reviewer 指摘）
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    return conn


def init_db(db_path: Path | str | None = None) -> Path:
    """DBファイルを作成し、4テーブル（profile / memories / memory_vec / history）を用意する"""
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection(path)
    try:
        for stmt in SCHEMA_STATEMENTS:
            conn.execute(stmt)

        # vec0 仮想テーブル（memories.id と 1:1）
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                memory_id INTEGER PRIMARY KEY,
                embedding float[1024] distance_metric=cosine
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    return path


def list_tables(db_path: Path | str | None = None) -> list[str]:
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table') ORDER BY name"
        ).fetchall()
        return [row["name"] for row in rows]
    finally:
        conn.close()


if __name__ == "__main__":
    db_file = init_db()
    tables = list_tables(db_file)
    expected = {
        "profile", "memories", "memory_vec", "history",
        "sessions", "archived_history", "open_threads", "state_audit",
    }
    found = expected.intersection(set(tables))
    if found == expected:
        print(f"テーブル8つ作成完了: {db_file}")
    else:
        print(f"警告: 期待テーブル {expected} / 実際 {set(tables)}")
