"""宿題箱: 裏方便の未処理ジョブを永続化する。設計書v2 §2.4(機会駆動), §2.6(状態目録)。

時刻指定バッチは組まない。Coreはenqueueで宿題を積むだけでよく、消化（①セッション終了時
②アイドル時 ③次回起動時の朝礼）はPhase4後続スライスの消化ロジックが担う。
永続先はserina_memory.db（長期記憶DB）とは別ファイル（§2.6: 宿題箱は長期記憶DBと別掲の状態）。
電源断・強制終了に耐えるよう、enqueue/mark_doneはそれぞれ即座にコミットする。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CHORE_BOX_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "chore_box.db"


@dataclass(frozen=True)
class ChoreJob:
    id: int
    kind: str
    lane: str  # "cloud" | "local"（§2.4 裏方便の二車線）
    payload: dict
    created_at: str


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChoreBox:
    """裏方便の宿題箱。処理済みジョブは削除する（§2.6: 永続・処理済みから削除）。"""

    def __init__(self, db_path: str | Path = DEFAULT_CHORE_BOX_PATH) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def enqueue(self, kind: str, *, lane: str, payload: dict) -> int:
        """宿題を1件積む。電源断に備え即座にコミットする。"""
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO chores (kind, lane, payload, created_at) VALUES (?, ?, ?, ?)",
                (kind, lane, json.dumps(payload, ensure_ascii=False), _utc_now_iso()),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def pending(self, *, kind: str | None = None, limit: int | None = None) -> list[ChoreJob]:
        """未処理の宿題を古い順に返す（消化順の裁量は呼び出し側）。"""
        conn = self._connect()
        try:
            query = "SELECT * FROM chores"
            params: list[str | int] = []
            if kind is not None:
                query += " WHERE kind = ?"
                params.append(kind)
            query += " ORDER BY id ASC"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [_row_to_job(row) for row in rows]
        finally:
            conn.close()

    def mark_done(self, job_id: int) -> None:
        """処理済みの宿題を削除する（§2.6: 処理済みから削除）。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM chores WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()

    def count(self, *, kind: str | None = None) -> int:
        conn = self._connect()
        try:
            if kind is not None:
                row = conn.execute("SELECT COUNT(*) AS c FROM chores WHERE kind = ?", (kind,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM chores").fetchone()
            return int(row["c"])
        finally:
            conn.close()


def _row_to_job(row: sqlite3.Row) -> ChoreJob:
    return ChoreJob(
        id=row["id"],
        kind=row["kind"],
        lane=row["lane"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )
