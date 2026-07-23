"""Fact 台帳。合意台帳 §3.3 / Wave 2。

時間付き事実の追加専用テーブル。会話中の即時 Fact 化は禁止（裏方便のみ）。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from serina.core.memory.ulid import new_ulid

FACT_STATUSES = frozenset({"active", "hypothesis", "superseded", "tombstone"})

# 2026-07-23: 蒸留プロンプトの抽出対象2分類（確定事実・約束 / 関係性・好みの持続的変化）に
# 合わせたfactの粗い分類。prefs_summary/relation_summary（core/chores/summaries.py）を
# facts台帳から組み立てる際の振り分けに使う。未指定（旧データ・LLMが省略した場合）はNone。
FACT_CATEGORY_FACT = "確定事実"
FACT_CATEGORY_PROMISE = "約束"
FACT_CATEGORY_PREFERENCE = "好み"
FACT_CATEGORY_RELATIONSHIP = "関係性"
FACT_CATEGORIES = frozenset({
    FACT_CATEGORY_FACT, FACT_CATEGORY_PROMISE, FACT_CATEGORY_PREFERENCE, FACT_CATEGORY_RELATIONSHIP,
})


class FactError(Exception):
    """Fact 台帳操作の拒否（昇格条件不足等）。"""


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    predicate: str
    object: str
    statement: str
    valid_from: str
    valid_to: str | None
    recorded_at: str
    confidence: float
    episode_ids: list[int]
    supersedes: str | None
    sensitivity: int
    importance: float
    status: str
    category: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Fact:
        episode_raw = row["episode_ids"]
        episode_ids = json.loads(episode_raw) if episode_raw else []
        keys = set(row.keys())
        return cls(
            id=row["id"],
            subject=row["subject"],
            predicate=row["predicate"],
            object=row["object"],
            statement=row["statement"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            recorded_at=row["recorded_at"],
            confidence=row["confidence"],
            episode_ids=episode_ids,
            supersedes=row["supersedes"],
            sensitivity=row["sensitivity"],
            importance=row["importance"],
            status=row["status"],
            category=row["category"] if "category" in keys else None,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_facts_schema(conn: sqlite3.Connection) -> None:
    """facts テーブルを追加のみで作成する。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS facts (
            id TEXT PRIMARY KEY,
            subject TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object TEXT NOT NULL,
            statement TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            recorded_at TEXT NOT NULL,
            confidence REAL NOT NULL,
            episode_ids TEXT NOT NULL DEFAULT '[]',
            supersedes TEXT,
            sensitivity INTEGER NOT NULL DEFAULT 2,
            importance REAL NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    # 2026-07-23追加: fact分類（確定事実/約束/好み/関係性）。既存DBには無い列のため
    # ALTER TABLEで追加する（追加のみ・安全な移行。core/chores/chore_box.pyと同じ手法）。
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(facts)").fetchall()}
    if "category" not in existing_cols:
        conn.execute("ALTER TABLE facts ADD COLUMN category TEXT")


class FactStore:
    """Fact 台帳 CRUD。MemoryStore と同一 DB パスを共有する。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            ensure_facts_schema(conn)
            conn.commit()
        finally:
            conn.close()

    def add_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object: str,
        statement: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float = 0.8,
        episode_ids: list[int] | None = None,
        supersedes: str | None = None,
        sensitivity: int = 2,
        importance: float = 0.5,
        status: str = "active",
        fact_id: str | None = None,
        category: str | None = None,
    ) -> str:
        """Fact を追加する。hypothesis→active は episode_ids 非空が必須。"""
        if status not in FACT_STATUSES:
            raise FactError(f"不正な status: {status}")
        if category is not None and category not in FACT_CATEGORIES:
            raise FactError(f"不正な category: {category}")
        episodes = list(episode_ids or [])
        if status == "active" and not episodes:
            raise FactError("active 昇格には episode_ids 非空が必須")

        now = _utc_now_iso()
        fid = fact_id or new_ulid()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO facts (
                    id, subject, predicate, object, statement,
                    valid_from, valid_to, recorded_at, confidence,
                    episode_ids, supersedes, sensitivity, importance, status, category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fid,
                    subject,
                    predicate,
                    object,
                    statement,
                    valid_from or now,
                    valid_to,
                    now,
                    confidence,
                    json.dumps(episodes, ensure_ascii=False),
                    supersedes,
                    sensitivity,
                    importance,
                    status,
                    category,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return fid

    def get_fact(self, fact_id: str) -> Fact | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        finally:
            conn.close()
        return Fact.from_row(row) if row else None

    def list_active_facts(self) -> list[Fact]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM facts WHERE status = 'active' ORDER BY recorded_at ASC"
            ).fetchall()
        finally:
            conn.close()
        return [Fact.from_row(row) for row in rows]

    def list_active_facts_by_category(self, category: str) -> list[Fact]:
        """指定categoryのactive factを返す（prefs_summary/relation_summaryの組み立て用）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM facts WHERE status = 'active' AND category = ? ORDER BY recorded_at ASC",
                (category,),
            ).fetchall()
        finally:
            conn.close()
        return [Fact.from_row(row) for row in rows]

    def list_active_facts_by_subject(self, subject: str) -> list[Fact]:
        """指定subjectと完全一致するactive factを返す（supersede判定の候補集め用）。

        `search_by_entity`のLIKE部分一致は「無関係な事実がobjectにたまたま含まれる」
        誤爆があり得るため、supersede候補の絞り込みには使わない（2026-07-23）。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM facts WHERE status = 'active' AND subject = ? ORDER BY recorded_at ASC",
                (subject,),
            ).fetchall()
        finally:
            conn.close()
        return [Fact.from_row(row) for row in rows]

    def search_by_entity(self, entity: str) -> list[Fact]:
        """entity 文字列を subject/object/statement に含む active fact を返す。"""
        if not entity.strip():
            return []
        pattern = f"%{entity.strip()}%"
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM facts
                WHERE status = 'active'
                  AND (subject LIKE ? OR object LIKE ? OR statement LIKE ?)
                ORDER BY recorded_at ASC
                """,
                (pattern, pattern, pattern),
            ).fetchall()
        finally:
            conn.close()
        return [Fact.from_row(row) for row in rows]

    def search_by_time_range(self, start_iso: str, end_iso: str) -> list[Fact]:
        """valid_from〜valid_to が指定区間と重なる active fact を返す。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT * FROM facts
                WHERE status = 'active'
                  AND valid_from <= ?
                  AND (valid_to IS NULL OR valid_to >= ?)
                ORDER BY valid_from ASC
                """,
                (end_iso, start_iso),
            ).fetchall()
        finally:
            conn.close()
        return [Fact.from_row(row) for row in rows]

    def supersede_fact(
        self,
        old_fact_id: str,
        *,
        subject: str,
        predicate: str,
        object: str,
        statement: str,
        valid_from: str | None = None,
        confidence: float = 0.8,
        episode_ids: list[int] | None = None,
        sensitivity: int = 2,
        importance: float = 0.5,
        status: str = "active",
        category: str | None = None,
    ) -> str:
        """新 fact を作成し、旧 fact を superseded にする。"""
        old = self.get_fact(old_fact_id)
        if old is None:
            raise FactError(f"旧 fact が見つからない: {old_fact_id}")
        if old.status == "tombstone":
            raise FactError("tombstone 済み fact は supersede できない")

        now = _utc_now_iso()
        new_id = self.add_fact(
            subject=subject,
            predicate=predicate,
            object=object,
            statement=statement,
            valid_from=valid_from or now,
            confidence=confidence,
            episode_ids=episode_ids or old.episode_ids,
            supersedes=old_fact_id,
            sensitivity=sensitivity,
            importance=importance,
            status=status,
            category=category or old.category,
        )

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE facts
                SET status = 'superseded', valid_to = ?
                WHERE id = ?
                """,
                (now, old_fact_id),
            )
            conn.commit()
        finally:
            conn.close()
        return new_id

    def tombstone_fact(self, fact_id: str, *, reason_valid_to: str | None = None) -> None:
        """fact を tombstone にする（物理削除しない）。"""
        now = reason_valid_to or _utc_now_iso()
        conn = self._connect()
        try:
            row = conn.execute("SELECT id FROM facts WHERE id = ?", (fact_id,)).fetchone()
            if row is None:
                raise FactError(f"fact が見つからない: {fact_id}")
            conn.execute(
                """
                UPDATE facts
                SET status = 'tombstone', valid_to = ?
                WHERE id = ?
                """,
                (now, fact_id),
            )
            conn.commit()
        finally:
            conn.close()

    def promote_hypothesis_to_active(self, fact_id: str, *, episode_ids: list[int]) -> None:
        """hypothesis を active に昇格する。episode_ids 非空必須。"""
        if not episode_ids:
            raise FactError("active 昇格には episode_ids 非空が必須")
        fact = self.get_fact(fact_id)
        if fact is None:
            raise FactError(f"fact が見つからない: {fact_id}")
        if fact.status != "hypothesis":
            raise FactError("hypothesis 以外は昇格対象外")

        conn = self._connect()
        try:
            conn.execute(
                """
                UPDATE facts
                SET status = 'active', episode_ids = ?
                WHERE id = ?
                """,
                (json.dumps(episode_ids, ensure_ascii=False), fact_id),
            )
            conn.commit()
        finally:
            conn.close()
