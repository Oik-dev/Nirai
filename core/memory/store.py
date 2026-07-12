"""記憶DBアクセス層。設計書 §4.2, §4.4, §1.3(Memory=Coreの内臓)

新規実装（旧memory/store.py, memory/db.pyは参照しない）。
既存の物理スキーマ（memoriesテーブル・memory_vec vec0仮想テーブル）は継承資産として踏襲する。
_ensure_schema()のCREATE TABLE文は実DBの物理スキーマ（tools/migrate_memory_schema.py適用後）と一致させてある。
想起: 関連度×新しさ×重要度のかけ算で上位のみ（§4.4）＋保護等級A/Sのキーワードトリガー想起。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite_vec

from serina.core.memory.embedder import OllamaEmbedder

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
    sensitivity_assessed: bool = False
    score: float = 0.0

    @classmethod
    def from_row(cls, row: sqlite3.Row, *, score: float = 0.0) -> MemoryRecord:
        """DB行から組み立てる（呼び出し箇所の重複畳み込み。2026-07-12監査）。"""
        return cls(
            id=row["id"],
            type=row["type"],
            content=row["content"],
            importance=row["importance"],
            sensitivity_grade=row["sensitivity_grade"],
            protection_grade=row["protection_grade"],
            cosmetic_version=row["cosmetic_version"],
            created_at=row["created_at"],
            last_accessed=row["last_accessed"],
            sensitivity_assessed=bool(row["sensitivity_assessed"]),
            score=score,
        )


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
    ) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._vector_dim = vector_dim
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
                    pinned INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_accessed TEXT NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT,
                    parent_id INTEGER,
                    metadata TEXT,
                    sensitivity_grade INTEGER NOT NULL DEFAULT 2,
                    cosmetic_version TEXT,
                    protection_grade TEXT NOT NULL DEFAULT 'B',
                    sensitivity_assessed INTEGER NOT NULL DEFAULT 0,
                    FOREIGN KEY (parent_id) REFERENCES memories(id)
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
            score = relevance * recency * importance  # §4.4: 関連度×新しさ×重要度の"かけ算"
            scored.append(MemoryRecord.from_row(row, score=score))
        scored.sort(key=lambda r: r.score, reverse=True)
        top = scored[:top_k]

        if top:
            self._refresh_access(record_ids=[r.id for r in top], now=now)

        return top

    def _refresh_access(self, *, record_ids: list[int], now: datetime) -> None:
        """想起された記憶の鮮度を回復する（§4.1: 想起されるたび鮮度回復）。"""
        conn = self._connect()
        try:
            conn.executemany(
                "UPDATE memories SET last_accessed = ?, access_count = access_count + 1 WHERE id = ?",
                [(now.isoformat(), record_id) for record_id in record_ids],
            )
            conn.commit()
        finally:
            conn.close()

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
        return MemoryRecord.from_row(row), relevance

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
        return [MemoryRecord.from_row(row) for row in rows]

    def get_unassessed_memories(
        self,
        *,
        exclude_protection_grade: str = "S",
        limit: int = 1,
        exclude_ids: set[int] | None = None,
    ) -> list[MemoryRecord]:
        """機微未査定の記憶を古い順に取得する（§4.6-3、Auroraのアイドル仕事の入力）。

        正典由来の固定9件（既定で保護等級S）は査定対象外（マスター確認済み）。
        exclude_ids: 2026-07-12追加。棚上げ棚（毒饅頭ジョブの先頭詰まり対策）に移された
        記憶idを除外する。正典の記憶DB(`memories`テーブル)にはカラムを追加せず、棚上げ状態は
        `chore_box.db`側で管理するため、除外はここで渡されたidセットに対してのみ行う
        （advisorレビュー2026-07-12）。
        """
        exclude_ids = exclude_ids or set()
        conn = self._connect()
        try:
            if exclude_ids:
                placeholders = ",".join("?" for _ in exclude_ids)
                rows = conn.execute(
                    f"""
                    SELECT * FROM memories
                    WHERE sensitivity_assessed = 0 AND protection_grade != ?
                      AND id NOT IN ({placeholders})
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (exclude_protection_grade, *sorted(exclude_ids), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE sensitivity_assessed = 0 AND protection_grade != ?
                    ORDER BY created_at ASC
                    LIMIT ?
                    """,
                    (exclude_protection_grade, limit),
                ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def list_memories_since(self, *, since_iso: str, exclude_type: str | None = None) -> list[MemoryRecord]:
        """`since_iso`以降に作られた記憶を古い順に返す（§4.5 日記材料: 当日の蒸留断片＋
        採用された記憶候補の取得に使う。§4.1「書き込みはこのライン一本」のため両者は同じ集合）。

        `exclude_type`は日記本文自体（type="diary"）を材料に混ぜて自己言及させないための除外。
        """
        conn = self._connect()
        try:
            if exclude_type is None:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE created_at >= ? ORDER BY created_at ASC",
                    (since_iso,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM memories WHERE created_at >= ? AND type != ? ORDER BY created_at ASC",
                    (since_iso, exclude_type),
                ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def list_by_type(self, type: str, *, limit: int = 200) -> list[MemoryRecord]:
        """指定typeの記憶を新しい順に返す（例: GUIの日記アルバム表示 type="diary"）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM memories WHERE type = ? ORDER BY created_at DESC LIMIT ?",
                (type, limit),
            ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def update_sensitivity(self, memory_id: int, *, grade: int, cosmetic_version: str | None) -> None:
        """機微査定の結果を反映する（§4.6-3）。等級2には化粧版を持たせない（§4.2）。"""
        stored_cosmetic = cosmetic_version if grade == 1 else None
        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE memories
                SET sensitivity_grade = ?, cosmetic_version = ?, sensitivity_assessed = 1
                WHERE id = ?
                """,
                (grade, stored_cosmetic, memory_id),
            )
            conn.commit()
        finally:
            conn.close()
