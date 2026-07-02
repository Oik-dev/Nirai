"""記憶 API（CRUD + ハイブリッド検索）"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlite_vec import serialize_float32

from serina.connectors.embedder import Embedder
from serina.memory.config import SearchConfig
from serina.memory.db import DEFAULT_DB_PATH, get_connection, init_db


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    if data.get("metadata"):
        try:
            data["metadata"] = json.loads(data["metadata"])
        except json.JSONDecodeError:
            pass
    data["pinned"] = bool(data.get("pinned"))
    return data


class MemoryStore:
    """Memory層の CRUD と検索。判断ロジックは持たない。"""

    def __init__(
        self,
        embedder: Embedder,
        db_path: Path | str | None = None,
        search_config: SearchConfig | None = None,
    ) -> None:
        self.embedder = embedder
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.search_config = search_config or SearchConfig()
        init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        return get_connection(self.db_path)

    def add_memory(
        self,
        type: str,
        content: str,
        importance: float,
        metadata: dict[str, Any] | None,
        source: str | None,
        pinned: bool = False,
        parent_id: int | None = None,
        created_at: str | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        now = created_at or _utc_now_iso()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        if embedding is None:
            embedding = self.embedder.embed(content)

        conn = self._conn()
        try:
            cur = conn.execute(
                """
                INSERT INTO memories
                    (type, content, importance, pinned, created_at, last_accessed,
                     access_count, source, parent_id, metadata)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    type,
                    content,
                    float(importance),
                    1 if pinned else 0,
                    now,
                    now,
                    source,
                    parent_id,
                    metadata_json,
                ),
            )
            memory_id = int(cur.lastrowid)
            conn.execute(
                "INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                (memory_id, serialize_float32(embedding)),
            )
            conn.commit()
            return memory_id
        finally:
            conn.close()

    def get(self, memory_id: int) -> dict[str, Any] | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()

    def update(
        self,
        memory_id: int,
        *,
        type: str | None = None,
        content: str | None = None,
        importance: float | None = None,
        metadata: dict[str, Any] | None = None,
        source: str | None = None,
        pinned: bool | None = None,
        parent_id: int | None = None,
    ) -> bool:
        existing = self.get(memory_id)
        if not existing:
            return False

        new_type = type if type is not None else existing["type"]
        new_content = content if content is not None else existing["content"]
        new_importance = importance if importance is not None else existing["importance"]
        new_metadata = metadata if metadata is not None else existing.get("metadata") or {}
        new_source = source if source is not None else existing.get("source")
        new_pinned = pinned if pinned is not None else existing["pinned"]
        new_parent = parent_id if parent_id is not None else existing.get("parent_id")

        reembed = content is not None and content != existing["content"]
        embedding = self.embedder.embed(new_content) if reembed else None

        conn = self._conn()
        try:
            conn.execute(
                """
                UPDATE memories
                SET type = ?, content = ?, importance = ?, pinned = ?,
                    source = ?, parent_id = ?, metadata = ?
                WHERE id = ?
                """,
                (
                    new_type,
                    new_content,
                    float(new_importance),
                    1 if new_pinned else 0,
                    new_source,
                    new_parent,
                    json.dumps(new_metadata, ensure_ascii=False),
                    memory_id,
                ),
            )
            if reembed and embedding is not None:
                conn.execute(
                    "UPDATE memory_vec SET embedding = ? WHERE memory_id = ?",
                    (serialize_float32(embedding), memory_id),
                )
            conn.commit()
            return True
        finally:
            conn.close()

    def delete(self, memory_id: int) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (memory_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def pin(self, memory_id: int) -> bool:
        return self._set_pinned(memory_id, True)

    def unpin(self, memory_id: int) -> bool:
        return self._set_pinned(memory_id, False)

    def _set_pinned(self, memory_id: int, pinned: bool) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute(
                "UPDATE memories SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, memory_id),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def _recency_score(self, last_accessed: str, pinned: bool, now: datetime) -> float:
        if pinned:
            return 1.0
        elapsed = (now - _parse_iso(last_accessed)).total_seconds()
        half_life = self.search_config.recency_half_life_days * 86400
        if half_life <= 0:
            return 1.0
        decay = math.log(2) / half_life
        return math.exp(-decay * max(0.0, elapsed))

    def _relevance_from_distance(self, distance: float) -> float:
        # cosine distance: 0=同一, 2=正反対
        return max(0.0, 1.0 - distance / 2.0)

    def _hybrid_score(
        self,
        relevance: float,
        recency: float,
        importance: float,
    ) -> float:
        cfg = self.search_config
        return cfg.alpha * relevance + cfg.beta * recency + cfg.gamma * float(importance)

    def search(
        self,
        query: str,
        k: int,
        type: str | None = None,
    ) -> list[dict[str, Any]]:
        if k <= 0:
            return []

        query_vec = self.embedder.embed(query)
        now = datetime.now(timezone.utc)
        cfg = self.search_config
        candidate_limit = max(k * cfg.vector_candidate_multiplier, k)

        conn = self._conn()
        try:
            vector_rows = conn.execute(
                """
                WITH knn_matches AS (
                    SELECT memory_id, distance
                    FROM memory_vec
                    WHERE embedding MATCH ?
                      AND k = ?
                )
                SELECT km.memory_id, km.distance
                FROM knn_matches km
                JOIN memories m ON m.id = km.memory_id
                WHERE (? IS NULL OR m.type = ?)
                ORDER BY km.distance
                """,
                (serialize_float32(query_vec), candidate_limit, type, type),
            ).fetchall()

            pinned_rows = conn.execute(
                """
                SELECT * FROM memories
                WHERE pinned = 1
                  AND (? IS NULL OR type = ?)
                """,
                (type, type),
            ).fetchall()

            candidates: dict[int, dict[str, Any]] = {}

            for row in vector_rows:
                memory_id = int(row["memory_id"])
                mem = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
                if not mem:
                    continue
                candidates[memory_id] = {
                    "memory": _row_to_dict(mem),
                    "distance": float(row["distance"]),
                }

            for row in pinned_rows:
                memory_id = int(row["id"])
                if memory_id not in candidates:
                    candidates[memory_id] = {
                        "memory": _row_to_dict(row),
                        "distance": 2.0,
                    }

            scored: list[tuple[float, dict[str, Any]]] = []
            for item in candidates.values():
                mem = item["memory"]
                relevance = self._relevance_from_distance(item["distance"])
                recency = self._recency_score(mem["last_accessed"], mem["pinned"], now)
                score = self._hybrid_score(relevance, recency, mem["importance"])
                result = dict(mem)
                result["score"] = score
                result["relevance"] = relevance
                result["recency"] = recency
                scored.append((score, result))

            scored.sort(key=lambda x: x[0], reverse=True)
            top = [item[1] for item in scored[:k]]

            # 想起した記憶の鮮度を更新
            now_iso = _utc_now_iso()
            for mem in top:
                conn.execute(
                    """
                    UPDATE memories
                    SET last_accessed = ?, access_count = access_count + 1
                    WHERE id = ?
                    """,
                    (now_iso, mem["id"]),
                )
            conn.commit()
            return top
        finally:
            conn.close()

    def add_history(self, session_id: str, role: str, content: str) -> int:
        conn = self._conn()
        try:
            ts = _utc_now_iso()
            cur = conn.execute(
                """
                INSERT INTO history (session_id, role, content, ts)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, role, content, ts),
            )
            # セッションが存在すれば最終発言時刻を更新（無ければ何もしない）
            conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE id = ?",
                (ts, session_id),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def get_recent_history(self, session_id: str, n: int) -> list[dict[str, Any]]:
        conn = self._conn()
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

    def get_profile(self, key: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT value FROM profile WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    def set_profile(self, key: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO profile (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, _utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def count_memories(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM memories").fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def count_pinned(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM memories WHERE pinned = 1").fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def get_all_pinned(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM memories WHERE pinned = 1 ORDER BY importance DESC, id ASC"
            ).fetchall()
            return [_row_to_dict(row) for row in rows]
        finally:
            conn.close()

    def find_similar(
        self,
        embedding: list[float],
        threshold: float,
        limit: int = 5,
    ) -> list[tuple[int, float, dict[str, Any]]]:
        """移行時の重複検出用。類似度（0-1）降順で返す"""
        conn = self._conn()
        try:
            rows = conn.execute(
                """
                SELECT memory_id, distance
                FROM memory_vec
                WHERE embedding MATCH ?
                  AND k = ?
                ORDER BY distance
                """,
                (serialize_float32(embedding), limit),
            ).fetchall()

            results: list[tuple[int, float, dict[str, Any]]] = []
            for row in rows:
                similarity = self._relevance_from_distance(float(row["distance"]))
                if similarity >= threshold:
                    mem = conn.execute(
                        "SELECT * FROM memories WHERE id = ?",
                        (int(row["memory_id"]),),
                    ).fetchone()
                    if mem:
                        results.append((int(row["memory_id"]), similarity, _row_to_dict(mem)))
            return results
        finally:
            conn.close()

    def merge_into(
        self,
        target_id: int,
        source: dict[str, Any],
        *,
        source_priority: dict[str, int] | None = None,
        canonical_sources: tuple[str, ...] | None = None,
    ) -> None:
        """既存記憶へ情報を統合（移行 dedup 用）。正典は保護し、破棄した本文は variants に退避。"""
        target = self.get(target_id)
        if not target:
            return

        if canonical_sources and target.get("source") in canonical_sources:
            return

        def _priority(source_name: str | None) -> int:
            if not source_name or not source_priority:
                return 1
            return int(source_priority.get(source_name, 1))

        target_content = str(target.get("content", ""))
        source_content = str(source.get("content", ""))
        target_pri = _priority(target.get("source"))
        source_pri = _priority(source.get("source"))

        variants: list[str] = list((target.get("metadata") or {}).get("variants") or [])

        if target_pri > source_pri:
            merged_content = target_content
            if source_content and source_content.strip() != target_content.strip():
                variants.append(source_content)
        elif source_pri > target_pri:
            merged_content = source_content
            if target_content.strip() != source_content.strip():
                variants.append(target_content)
        elif len(source_content) > len(target_content):
            merged_content = source_content
            if target_content.strip() != source_content.strip():
                variants.append(target_content)
        else:
            merged_content = target_content
            if source_content.strip() and source_content.strip() != target_content.strip():
                variants.append(source_content)

        target_meta = dict(target.get("metadata") or {})
        source_meta = dict(source.get("metadata") or {})
        merged_meta = {**target_meta, **source_meta}
        if variants:
            merged_meta["variants"] = variants
        if "merged_sources" not in merged_meta:
            merged_meta["merged_sources"] = []
        for src in filter(None, [target.get("source"), source.get("source")]):
            if src not in merged_meta["merged_sources"]:
                merged_meta["merged_sources"].append(src)

        merged_importance = max(float(target["importance"]), float(source.get("importance", 0)))
        merged_pinned = bool(target["pinned"]) or bool(source.get("pinned"))

        self.update(
            target_id,
            content=merged_content,
            importance=merged_importance,
            metadata=merged_meta,
            pinned=merged_pinned,
        )

    # ---- セッション管理（3a） ----

    def create_session(self, session_id: str, now: str | None = None) -> None:
        ts = now or _utc_now_iso()
        conn = self._conn()
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
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM sessions WHERE status = 'active' "
                "ORDER BY last_activity DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def list_sessions_by_status(self, status: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE status = ? ORDER BY last_activity ASC",
                (status,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def set_session_status(
        self, session_id: str, status: str, distilled_at: str | None = None
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE sessions SET status = ?, distilled_at = ? WHERE id = ?",
                (status, distilled_at, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def touch_session_activity(self, session_id: str, ts: str | None = None) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE sessions SET last_activity = ? WHERE id = ?",
                (ts or _utc_now_iso(), session_id),
            )
            conn.commit()
        finally:
            conn.close()

    def archive_session_history(self, session_id: str) -> int:
        """history の該当セッション行を archived_history へ移動し、history からは削除。移動件数を返す。"""
        conn = self._conn()
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
        conn = self._conn()
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

    def purge_archived_older_than(self, days: int) -> int:
        """退避してから（archived_at が）days 日より古い archived_history を物理削除。削除件数を返す。

        ts（元発言時刻）基準にすると、古いセッションを退避した直後に保持バッファなしで
        消えてしまうため、必ず退避時刻を基準にする。
        """
        conn = self._conn()
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            cur = conn.execute("DELETE FROM archived_history WHERE archived_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
