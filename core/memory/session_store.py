"""GUI会話帳簿（sessions / history / archived_history）。

正典記憶（memories）の審査・保護とは別口。同一物理DBに同居してよいが、
書き込み経路は混ぜない（憲章・architecture-reviewer 2026-07-12 衛生事項）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_SESSION_DB_PATH = (
    Path(__file__).resolve().parent.parent.parent / "data" / "serina_memory.db"
)

_SCHEMA = [
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
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """セッションID・会話履歴の帳簿。埋め込み・memories には触れない。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_SESSION_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            for stmt in _SCHEMA:
                conn.execute(stmt)
            conn.commit()
        finally:
            conn.close()

    def add_history(self, session_id: str, role: str, content: str) -> int:
        conn = self._connect()
        try:
            ts = _utc_now_iso()
            cur = conn.execute(
                """
                INSERT INTO history (session_id, role, content, ts)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, ts),
            )
            conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE id = ?",
                (ts, session_id),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_recent_history(self, session_id: str, n: int) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM history
                WHERE session_id = ?
                ORDER BY ts DESC
                LIMIT ?
                """,
                (session_id, n),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]
        finally:
            conn.close()

    def get_session_history(self, session_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM history WHERE session_id = ? ORDER BY ts ASC, id ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_archived_history(self, session_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM archived_history WHERE session_id = ? "
                "ORDER BY ts ASC, id ASC",
                (session_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def create_session(self, session_id: str, now: str | None = None) -> None:
        ts = now or _utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO sessions (id, status, created_at, last_activity) "
                "VALUES (?, 'active', ?, ?)",
                (session_id, ts, ts),
            )
            conn.commit()
        finally:
            conn.close()

    def get_active_session(self) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE status = 'active' "
                "ORDER BY last_activity DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_sessions_by_status(self, status: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY last_activity ASC",
                (status,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_sessions(self, limit: int = 100) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY last_activity DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def list_session_previews(self, limit: int = 50) -> list[dict[str, Any]]:
        sessions = self.list_sessions(limit=limit)
        result: list[dict[str, Any]] = []
        conn = self._connect()
        try:
            for s in sessions:
                sid = s["id"]
                row = conn.execute(
                    "SELECT content FROM history WHERE session_id = ? AND role = 'user' "
                    "ORDER BY ts ASC, id ASC LIMIT 1",
                    (sid,),
                ).fetchone()
                if row is None:
                    row = conn.execute(
                        "SELECT content FROM archived_history WHERE session_id = ? "
                        "AND role = 'user' ORDER BY ts ASC, id ASC LIMIT 1",
                        (sid,),
                    ).fetchone()
                count_row = conn.execute(
                    "SELECT COUNT(*) AS c FROM history WHERE session_id = ?",
                    (sid,),
                ).fetchone()
                msg_count = int(count_row["c"]) if count_row else 0
                if msg_count == 0:
                    arch = conn.execute(
                        "SELECT COUNT(*) AS c FROM archived_history WHERE session_id = ?",
                        (sid,),
                    ).fetchone()
                    msg_count = int(arch["c"]) if arch else 0
                preview = (row["content"] if row else "")[:40]
                result.append(
                    {
                        "id": sid,
                        "status": s["status"],
                        "created_at": s["created_at"],
                        "last_activity": s["last_activity"],
                        "preview": preview,
                        "empty": msg_count == 0,
                    }
                )
        finally:
            conn.close()
        return result

    def set_session_status(
        self, session_id: str, status: str, distilled_at: str | None = None
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET status = ?, distilled_at = ? WHERE id = ?",
                (status, distilled_at, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def touch_session_activity(self, session_id: str, ts: str | None = None) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE id = ?",
                (ts or _utc_now_iso(), session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def archive_session_history(self, session_id: str) -> int:
        conn = self._connect()
        try:
            now = _utc_now_iso()
            rows = conn.execute(
                "SELECT id, session_id, role, content, ts FROM history WHERE session_id = ?",
                (session_id,),
            ).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO archived_history "
                    "(id, session_id, role, content, ts, archived_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (r["id"], r["session_id"], r["role"], r["content"], r["ts"], now),
                )
            conn.execute("DELETE FROM history WHERE session_id = ?", (session_id,))
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    def count_archived(self, session_id: str | None = None) -> int:
        conn = self._connect()
        try:
            if session_id is None:
                row = conn.execute("SELECT COUNT(*) AS c FROM archived_history").fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM archived_history WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def backfill_orphan_sessions(self) -> list[str]:
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT h.session_id, MIN(h.ts) AS first_ts, MAX(h.ts) AS last_ts
                FROM history h LEFT JOIN sessions s ON s.id = h.session_id
                WHERE s.id IS NULL
                GROUP BY h.session_id
                """
            ).fetchall()
            for r in rows:
                conn.execute(
                    "INSERT OR IGNORE INTO sessions (id, status, created_at, last_activity) "
                    "VALUES (?, 'pending', ?, ?)",
                    (r["session_id"], r["first_ts"], r["last_ts"]),
                )
            conn.commit()
            return [r["session_id"] for r in rows]
        finally:
            conn.close()

    def purge_archived_older_than(self, days: int) -> int:
        conn = self._connect()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            cur = conn.execute(
                "DELETE FROM archived_history WHERE archived_at < ?", (cutoff,)
            )
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
