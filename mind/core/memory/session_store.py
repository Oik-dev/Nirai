"""GUI会話帳簿（sessions / history / archived_history。イデアの data/ledger.db）。

画面のための帳簿（セッションの区切り・発言のID）。会話の正本はイデアの生ログ（core/lifelog.py）。
帳簿に書いた発言は生ログにも書き、帳簿から消すのはMasterが明示したときだけで、そのときは生ログからも消す。
「帳簿にある発言は、必ず生ログにもある」を保つ（sync_conversation_log で取りこぼしを埋める）。
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from mind.core import debug_log
from mind.core.idea import DATA_DIR, RESIDENT_NAME
from mind.core.lifelog import MASTER, ConversationLog

DEFAULT_SESSION_DB_PATH = DATA_DIR / "ledger.db"

logger = logging.getLogger(__name__)


def _speaker(role: str) -> str:
    """帳簿の role を、生ログの話者の名前にする。"""
    return {"user": MASTER, "assistant": RESIDENT_NAME}.get(role, role)


def _log_line(row: Any) -> dict[str, str]:
    return {
        "ts": row["ts"],
        "session": row["session_id"],
        "speaker": _speaker(row["role"]),
        "text": row["content"],
    }

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

    def __init__(
        self,
        db_path: Path | str | None = None,
        conversation_log: ConversationLog | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else DEFAULT_SESSION_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conversation_log = conversation_log or ConversationLog()
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
            message_id = int(cur.lastrowid)
        finally:
            conn.close()
        try:
            self.conversation_log.append(
                ts=ts, session=session_id, speaker=_speaker(role), text=content,
            )
        except Exception as exc:  # noqa: BLE001
            # 会話は止めない。帳簿には残っているので、次の起動時の sync_conversation_log が埋める。
            logger.exception("生ログへの追記に失敗（次回起動時に帳簿から埋めます）")
            debug_log.emit(kind="lifelog", action="append_failed", error=type(exc).__name__, detail=str(exc))
        return message_id

    def sync_conversation_log(self) -> int:
        """帳簿にあって生ログにない発言を、生ログへ書き足す。足した件数を返す。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT session_id, role, content, ts FROM history "
                "UNION ALL SELECT session_id, role, content, ts FROM archived_history"
            ).fetchall()
        finally:
            conn.close()
        return self.conversation_log.add_missing(_log_line(row) for row in rows)

    def last_master_spoke_at(self) -> datetime | None:
        """帳簿全体（片付けたセッションも含む）で、Masterが最後に話した時刻。まだ一度もなければ None。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT MAX(ts) FROM (SELECT ts FROM history WHERE role = 'user' "
                "UNION ALL SELECT ts FROM archived_history WHERE role = 'user')"
            ).fetchone()
        finally:
            conn.close()
        return datetime.fromisoformat(row[0]) if row[0] else None

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

    def get_message(self, message_id: int) -> dict[str, Any] | None:
        """history / archived_history から1行取得。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, session_id, role, content, ts FROM history WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT id, session_id, role, content, ts FROM archived_history WHERE id = ?",
                    (message_id,),
                ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_message(self, message_id: int) -> dict[str, Any] | None:
        """1発言を生ログと history / archived_history から物理削除（Masterが明示したときだけ）。

        生ログから先に消す。生ログで失敗したら例外のまま止め、帳簿にも残す（消えたと誤認させない）。
        戻り値の erased は、生ログで消した行（日のファイル名, 行番号）。記憶のページを外すのに使う。
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, session_id, role, content, ts FROM history WHERE id = ?",
                (message_id,),
            ).fetchone()
            table = "history"
            if row is None:
                row = conn.execute(
                    "SELECT id, session_id, role, content, ts FROM archived_history WHERE id = ?",
                    (message_id,),
                ).fetchone()
                table = "archived_history"
            if row is None:
                return None
            line = _log_line(row)
            erased = self.conversation_log.remove(
                session=line["session"], ts=line["ts"], speaker=line["speaker"], text=line["text"],
            )
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (message_id,))
            conn.commit()
            return {**dict(row), "erased": erased}
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> dict[str, Any]:
        """セッションの会話を物理削除する（マスター手動メンテ用）。

        生ログのそのセッションの発言を先に消し、次に history / archived_history / sessions 行を消す。
        記憶のページには触れない（戻り値の erased で、呼び出し側が外す）。現行 active セッションの拒否と
        変更レポートは呼び出し側で行うこと。
        """
        erased = self.conversation_log.remove(session=session_id)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, role, ts, substr(content, 1, 120) AS c FROM history "
                "WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    "SELECT id, role, ts, substr(content, 1, 120) AS c FROM archived_history "
                    "WHERE session_id = ? ORDER BY id",
                    (session_id,),
                ).fetchall()
            preview = "\n".join(
                f"{r['id']}|{r['role']}|{r['ts']}|{r['c']}" for r in rows
            )
            n_h = conn.execute(
                "DELETE FROM history WHERE session_id = ?", (session_id,)
            ).rowcount
            n_a = conn.execute(
                "DELETE FROM archived_history WHERE session_id = ?", (session_id,)
            ).rowcount
            n_s = conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            ).rowcount
            conn.commit()
            return {
                "session_id": session_id,
                "history_deleted": int(n_h),
                "archived_deleted": int(n_a),
                "session_deleted": int(n_s),
                "preview": preview,
                "erased": erased,
            }
        finally:
            conn.close()
