"""宿題箱: 裏方便の未処理ジョブを永続化する。設計書 §2.4(機会駆動), §2.6(状態目録)。

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
    failure_count: int = 0


@dataclass(frozen=True)
class ShelvedJob:
    """棚上げ棚（毒饅頭ジョブ）。2026-07-12決定: 失敗回数記録→車線振替→それでも失敗なら
    棚上げ＋GUI通知＋日本語レポート（原則1: 無言破棄禁止。DECISIONS参照）。"""

    id: int
    kind: str
    lane: str
    payload: dict
    created_at: str
    shelved_at: str
    reason: str


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
            # 2026-07-12追加: 失敗回数の記録（毒饅頭ジョブの先頭詰まり対策）。
            # 既存DBには無いカラムのためALTER TABLEで追加する（追加のみ・安全な移行）。
            existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chores)").fetchall()}
            if "failure_count" not in existing_cols:
                conn.execute("ALTER TABLE chores ADD COLUMN failure_count INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shelf (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    lane TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    shelved_at TEXT NOT NULL,
                    reason TEXT NOT NULL
                )
                """
            )
            # 機微査定(§4.6-3)は車線が"local"1本のみのため、蒸留ジョブのような車線振替が
            # できない。失敗回数だけをmemory_id単位で記録し、既定回数で棚上げする
            # （chore_box.dbは§2.6「長期記憶DBと別掲の裏方便状態」のため、正典の記憶DB
            # 側にはカラムを追加しない。advisorレビュー2026-07-12）。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS assessment_failures (
                    memory_id INTEGER PRIMARY KEY,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    shelved INTEGER NOT NULL DEFAULT 0,
                    shelved_at TEXT,
                    reason TEXT
                )
                """
            )
            # Wave 5 §4-2: 裏方仕事の checkpoint（処理済み id 集合を永続化し再開可能にする）
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chore_checkpoints (
                    scope TEXT NOT NULL,
                    work_key TEXT NOT NULL,
                    processed_ids TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (scope, work_key)
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

    # --- 2026-07-12追加: 失敗回数の記録・車線振替・棚上げ棚（毒饅頭ジョブの先頭詰まり対策） ---

    def increment_failure(self, job_id: int) -> int:
        """ジョブの失敗回数を1増やし、更新後の値を返す。"""
        conn = self._connect()
        try:
            conn.execute("UPDATE chores SET failure_count = failure_count + 1 WHERE id = ?", (job_id,))
            conn.commit()
            row = conn.execute("SELECT failure_count FROM chores WHERE id = ?", (job_id,)).fetchone()
            return int(row["failure_count"]) if row is not None else 0
        finally:
            conn.close()

    def switch_lane(self, job_id: int, new_lane: str) -> None:
        """車線振替による自動再挑戦（3回失敗→振替。振替後は失敗回数を0にリセットし、
        新しい車線での再挑戦に公平な機会を与える）。"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE chores SET lane = ?, failure_count = 0 WHERE id = ?", (new_lane, job_id)
            )
            conn.commit()
        finally:
            conn.close()

    def shelve(self, job_id: int, *, reason: str) -> None:
        """車線振替後もなお失敗するジョブを棚上げ棚へ移動する（原則1: 無言破棄禁止のため
        reasonを伴わせる。呼び出し側がChangeLog等へ日本語レポートも残すこと）。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM chores WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                return
            conn.execute(
                "INSERT INTO shelf (kind, lane, payload, created_at, shelved_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["kind"], row["lane"], row["payload"], row["created_at"], _utc_now_iso(), reason),
            )
            conn.execute("DELETE FROM chores WHERE id = ?", (job_id,))
            conn.commit()
        finally:
            conn.close()

    def shelved(self, *, kind: str | None = None, limit: int | None = None) -> list[ShelvedJob]:
        conn = self._connect()
        try:
            query = "SELECT * FROM shelf"
            params: list[str | int] = []
            if kind is not None:
                query += " WHERE kind = ?"
                params.append(kind)
            query += " ORDER BY shelved_at DESC"
            if limit is not None:
                query += " LIMIT ?"
                params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [
                ShelvedJob(
                    id=row["id"], kind=row["kind"], lane=row["lane"],
                    payload=json.loads(row["payload"]), created_at=row["created_at"],
                    shelved_at=row["shelved_at"], reason=row["reason"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def shelved_count(self, *, kind: str | None = None) -> int:
        conn = self._connect()
        try:
            if kind is not None:
                row = conn.execute("SELECT COUNT(*) AS c FROM shelf WHERE kind = ?", (kind,)).fetchone()
            else:
                row = conn.execute("SELECT COUNT(*) AS c FROM shelf").fetchone()
            return int(row["c"])
        finally:
            conn.close()

    # --- 2026-07-12追加: 機微査定(§4.6-3)の失敗回数記録・棚上げ（車線が"local"1本のみのため
    # 車線振替は行わず、既定回数連続で失敗したら直接棚上げる） ---

    def note_assessment_failure(self, memory_id: int, *, reason: str | None = None) -> int:
        """既存記憶1件の機微査定失敗を記録し、更新後の失敗回数を返す。

        reasonを渡した場合は、棚上げ前でも直近の失敗理由として`reason`列を更新する
        （原則1: 診断可能な証跡を残す）。
        """
        conn = self._connect()
        try:
            if reason is not None:
                conn.execute(
                    "INSERT INTO assessment_failures (memory_id, failure_count, reason) "
                    "VALUES (?, 1, ?) "
                    "ON CONFLICT(memory_id) DO UPDATE SET "
                    "failure_count = failure_count + 1, reason = excluded.reason",
                    (memory_id, reason),
                )
            else:
                conn.execute(
                    "INSERT INTO assessment_failures (memory_id, failure_count) VALUES (?, 1) "
                    "ON CONFLICT(memory_id) DO UPDATE SET failure_count = failure_count + 1",
                    (memory_id,),
                )
            conn.commit()
            row = conn.execute(
                "SELECT failure_count FROM assessment_failures WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            return int(row["failure_count"]) if row is not None else 0
        finally:
            conn.close()

    def assessment_failure_reason(self, memory_id: int) -> str | None:
        """直近の査定失敗理由（未記録ならNone）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT reason FROM assessment_failures WHERE memory_id = ?", (memory_id,)
            ).fetchone()
            if row is None:
                return None
            reason = row["reason"]
            return str(reason) if reason else None
        finally:
            conn.close()

    def shelve_assessment(self, memory_id: int, *, reason: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE assessment_failures SET shelved = 1, shelved_at = ?, reason = ? "
                "WHERE memory_id = ?",
                (_utc_now_iso(), reason, memory_id),
            )
            conn.commit()
        finally:
            conn.close()

    def unshelve_assessments(self, memory_ids: list[int] | None = None) -> list[int]:
        """機微査定の棚上げを解除し、失敗回数もリセットする。

        memory_idsがNoneなら棚上げ中の全件。解除したmemory_idのリストを返す。
        """
        conn = self._connect()
        try:
            if memory_ids is None:
                rows = conn.execute(
                    "SELECT memory_id FROM assessment_failures WHERE shelved = 1"
                ).fetchall()
                targets = [int(row["memory_id"]) for row in rows]
            else:
                targets = list(memory_ids)
            if not targets:
                return []
            placeholders = ",".join("?" for _ in targets)
            conn.execute(
                f"DELETE FROM assessment_failures WHERE memory_id IN ({placeholders})",
                targets,
            )
            conn.commit()
            return targets
        finally:
            conn.close()

    def shelved_assessment_ids(self) -> set[int]:
        """棚上げ済みの記憶idを返す（get_unassessed_memoriesの除外に使う）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT memory_id FROM assessment_failures WHERE shelved = 1"
            ).fetchall()
            return {int(row["memory_id"]) for row in rows}
        finally:
            conn.close()

    def shelved_assessment_count(self) -> int:
        return len(self.shelved_assessment_ids())

    # --- Wave 5 §4-2: chore checkpoint（EverOS 由来。途中失敗・発話割り込みから再開） ---

    def get_checkpoint_processed_ids(self, scope: str, work_key: str) -> set[str]:
        """scope/work_key に紐づく処理済み id 集合。未記録なら空集合。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT processed_ids FROM chore_checkpoints WHERE scope = ? AND work_key = ?",
                (scope, work_key),
            ).fetchone()
            if row is None:
                return set()
            data = json.loads(row["processed_ids"])
            if not isinstance(data, list):
                return set()
            return {str(item) for item in data}
        finally:
            conn.close()

    def set_checkpoint_processed_ids(self, scope: str, work_key: str, processed_ids: set[str]) -> None:
        conn = self._connect()
        try:
            payload = json.dumps(sorted(processed_ids), ensure_ascii=False)
            conn.execute(
                "INSERT INTO chore_checkpoints (scope, work_key, processed_ids, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope, work_key) DO UPDATE SET "
                "processed_ids = excluded.processed_ids, updated_at = excluded.updated_at",
                (scope, work_key, payload, _utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def add_checkpoint_processed_id(self, scope: str, work_key: str, item_id: str) -> None:
        processed = self.get_checkpoint_processed_ids(scope, work_key)
        processed.add(str(item_id))
        self.set_checkpoint_processed_ids(scope, work_key, processed)

    def clear_checkpoint(self, scope: str, work_key: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM chore_checkpoints WHERE scope = ? AND work_key = ?",
                (scope, work_key),
            )
            conn.commit()
        finally:
            conn.close()


def _row_to_job(row: sqlite3.Row) -> ChoreJob:
    return ChoreJob(
        id=row["id"],
        kind=row["kind"],
        lane=row["lane"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
        failure_count=int(row["failure_count"]) if "failure_count" in row.keys() else 0,
    )
