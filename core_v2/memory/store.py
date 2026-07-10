"""記憶DBアクセス層。設計書v2 §4.2, §4.4, §1.3(Memory=Coreの内臓)

新規実装（旧memory/store.py, memory/db.pyは参照しない）。
既存の物理スキーマ（memoriesテーブル・memory_vec vec0仮想テーブル）は継承資産として踏襲する。
想起: 関連度×新しさ×重要度のかけ算で上位のみ（§4.4）＋保護等級A/Sのキーワードトリガー想起。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite_vec

from serina.core_v2.memory.embedder import OllamaEmbedder

VECTOR_DIM_DEFAULT = 1024


@dataclass(frozen=True)
class MemoryRecord:
    id: int
    type: str
    content: str
    importance: float
    sensitivity_grade: int
    protection_grade: str
    cosmetic_version: str | None
    created_at: str
    last_accessed: str
    score: float = 0.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_ts: str, now: datetime) -> float:
    dt = datetime.fromisoformat(iso_ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now - dt).total_seconds() / 86400.0)


class MemoryStore:
    def __init__(
        self,
        db_path: str,
        embedder: OllamaEmbedder,
        vector_dim: int = VECTOR_DIM_DEFAULT,
        relevance_weight: float = 0.5,
        recency_weight: float = 0.3,
        importance_weight: float = 0.2,
    ) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._vector_dim = vector_dim
        self._relevance_weight = relevance_weight
        self._recency_weight = recency_weight
        self._importance_weight = importance_weight
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance REAL NOT NULL DEFAULT 0.5,
                    sensitivity_grade INTEGER NOT NULL DEFAULT 2,
                    protection_grade TEXT NOT NULL DEFAULT 'B',
                    cosmetic_version TEXT,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
                    memory_id INTEGER PRIMARY KEY,
                    embedding float[{self._vector_dim}] distance_metric=cosine
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def add_memory(
        self,
        content: str,
        *,
        type: str,
        importance: float = 0.5,
        sensitivity_grade: int = 2,
        protection_grade: str = "B",
        cosmetic_version: str | None = None,
    ) -> int:
        now = _utc_now_iso()
        vector = self._embedder.embed(content)
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO memories
                    (type, content, importance, sensitivity_grade, protection_grade,
                     cosmetic_version, created_at, last_accessed, access_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (type, content, importance, sensitivity_grade, protection_grade, cosmetic_version, now, now),
            )
            memory_id = cursor.lastrowid
            conn.execute(
                "INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                (memory_id, sqlite_vec.serialize_float32(vector)),
            )
            conn.commit()
            return memory_id
        finally:
            conn.close()

    def recall(self, query_text: str, top_k: int = 5) -> list[MemoryRecord]:
        """関連度×新しさ×重要度のかけ算で上位top_k件を想起する（§4.4）。"""
        query_vector = self._embedder.embed(query_text)
        now = datetime.now(timezone.utc)
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.*, v.distance AS distance
                FROM memory_vec v
                JOIN memories m ON m.id = v.memory_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (sqlite_vec.serialize_float32(query_vector), max(top_k * 4, top_k)),
            ).fetchall()
        finally:
            conn.close()

        scored: list[MemoryRecord] = []
        for row in rows:
            relevance = max(0.0, 1.0 - row["distance"])
            recency = 1.0 / (1.0 + _days_since(row["last_accessed"], now))
            importance = row["importance"]
            score = (
                self._relevance_weight * relevance
                + self._recency_weight * recency
                + self._importance_weight * importance
            )
            scored.append(
                MemoryRecord(
                    id=row["id"],
                    type=row["type"],
                    content=row["content"],
                    importance=row["importance"],
                    sensitivity_grade=row["sensitivity_grade"],
                    protection_grade=row["protection_grade"],
                    cosmetic_version=row["cosmetic_version"],
                    created_at=row["created_at"],
                    last_accessed=row["last_accessed"],
                    score=score,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    def nearest_relevance(self, query_text: str) -> tuple[MemoryRecord, float] | None:
        """最も近い記憶と純粋な関連度(1-cosine距離)を返す。重複チェック専用（新しさ・重要度を混ぜない）。"""
        query_vector = self._embedder.embed(query_text)
        conn = self._connect()
        try:
            row = conn.execute(
                """
                SELECT m.*, v.distance AS distance
                FROM memory_vec v
                JOIN memories m ON m.id = v.memory_id
                WHERE v.embedding MATCH ? AND k = 1
                ORDER BY v.distance
                """,
                (sqlite_vec.serialize_float32(query_vector),),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return None

        relevance = max(0.0, 1.0 - row["distance"])
        record = MemoryRecord(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            importance=row["importance"],
            sensitivity_grade=row["sensitivity_grade"],
            protection_grade=row["protection_grade"],
            cosmetic_version=row["cosmetic_version"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
        )
        return record, relevance

    def recall_by_keyword(self, keyword: str) -> list[MemoryRecord]:
        """保護等級A/S（約束・正典級）はキーワードのトリガー想起で確実に拾う（§4.4）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE protection_grade IN ('A', 'S') AND content LIKE ?
                ORDER BY importance DESC
                """,
                (f"%{keyword}%",),
            ).fetchall()
        finally:
            conn.close()
        return [
            MemoryRecord(
                id=row["id"],
                type=row["type"],
                content=row["content"],
                importance=row["importance"],
                sensitivity_grade=row["sensitivity_grade"],
                protection_grade=row["protection_grade"],
                cosmetic_version=row["cosmetic_version"],
                created_at=row["created_at"],
                last_accessed=row["last_accessed"],
            )
            for row in rows
        ]
