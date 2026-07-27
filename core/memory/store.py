"""記憶DBアクセス層。設計書 §4.2, §4.4, §1.3(Memory=Coreの内臓)

新規実装（旧memory/store.py, memory/db.pyは参照しない）。
既存の物理スキーマ（memoriesテーブル・memory_vec vec0仮想テーブル）は継承資産として踏襲する。
_ensure_schema()のCREATE TABLE文は実DBの物理スキーマ（tools/migrate_memory_schema.py適用後）と一致させてある。
想起: 足し算の活性化モデル1本（基礎活性＋話題近接＋連想伝播＋ゆらぎ。§4.4 2026-07-17改訂）。
旧二経路（かけ算スコア＋trigger_keywordsトリガー想起）は撤去済み。控えはコミット7aba434。

公開面（合意台帳 §4-4 / B3）: `store.write`（書込）と `store.read`（読出）。
既存メソッドは互換のため残し、二口 API は薄い委譲とする。
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, overload


import sqlite_vec

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.facts import FactStore, ensure_facts_schema

VECTOR_DIM_DEFAULT = 1024


@dataclass(frozen=True)
class RecallParams:
    """§4.4活性化モデルのツマミ。本番値は config/thresholds.toml [recall]（§5.5-3）。

    既定値はテスト・単体利用向けの初期調整値。話題近接（weight_relevance）が
    全部品中で配点最大であること（§4.4付帯ルール）を崩さないこと。
    """

    weight_relevance: float = 0.6
    weight_importance: float = 0.15
    weight_recency: float = 0.05
    grade_bonus_s: float = 0.20
    grade_bonus_a: float = 0.15
    spread_decay: float = 0.5
    spread_seeds: int = 3
    noise_sigma: float = 0.02
    activation_floor: float = 0.5


@dataclass(frozen=True)
class RecallExplanation:
    """想起スコアの部品別内訳（合意台帳 OSS #5 / Wave 3 B5）。"""

    relevance: float
    importance: float
    recency: float
    grade_bonus: float
    spread: float
    noise: float
    activation: float


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
    source: str | None = None
    parent_id: int | None = None
    metadata: dict[str, Any] | None = None
    score: float = 0.0
    explanation: RecallExplanation | None = None

    @classmethod
    def from_row(
        cls,
        row: sqlite3.Row,
        *,
        score: float = 0.0,
        explanation: RecallExplanation | None = None,
    ) -> MemoryRecord:
        """DB行から組み立てる（呼び出し箇所の重複畳み込み。2026-07-12監査）。"""
        keys = set(row.keys())
        parent_raw = row["parent_id"] if "parent_id" in keys else None
        meta_raw = row["metadata"] if "metadata" in keys else None
        meta: dict[str, Any] | None = None
        if meta_raw is not None and str(meta_raw).strip():
            try:
                parsed = json.loads(meta_raw)
                if isinstance(parsed, dict):
                    meta = parsed
            except json.JSONDecodeError:
                meta = None
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
            source=row["source"] if "source" in keys else None,
            parent_id=int(parent_raw) if parent_raw is not None else None,
            metadata=meta,
            score=score,
            explanation=explanation,
        )

    @property
    def is_inherited(self) -> bool:
        """legacy 投入の原典記憶か（source 非空）。本番蒸留は source=null。"""
        return bool(self.source and str(self.source).strip())


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
        recall_params: RecallParams | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._db_path = db_path
        self._embedder = embedder
        self._vector_dim = vector_dim
        self._recall_params = recall_params or RecallParams()
        # ゆらぎ用の乱数源。テストはseed済みRandomを注入して再現可能にする
        self._rng = rng or random.Random()
        self._ensure_schema()

    def embed_text(self, text: str) -> list[float]:
        """bge-m3埋め込みを取る薄い公開口（facts台帳のsupersede類似度判定等、私有の_embedderに
        直接触れたくない呼び出し元向け。2026-07-23）。"""
        return self._embedder.embed(text)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.row_factory = sqlite3.Row
        # session_store.py と同定石。会話想起と裏方便の並行アクセスでロック待ちを減らす
        # （合意台帳 OSS #8。スキーマ変更ではない接続PRAGMAのみ）
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
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
            # 蒸留冪等キー（合意台帳 §4-1）。episode/turns 集合の content hash。
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS distillation_keys (
                    content_hash TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_tombstones (
                    memory_id INTEGER PRIMARY KEY,
                    tombstoned_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
                """
            )
            ensure_facts_schema(conn, vector_dim=self._vector_dim)
            conn.commit()
        finally:
            conn.close()

    @property
    def write(self) -> MemoryWriteAPI:
        """書込系公開口（B3）。"""
        return MemoryWriteAPI(self)

    @property
    def read(self) -> MemoryReadAPI:
        """読出系公開口（B3）。"""
        return MemoryReadAPI(self)

    def add_memory(
        self,
        content: str,
        *,
        type: str,
        importance: float = 0.5,
        sensitivity_grade: int = 2,
        protection_grade: str = "B",
        cosmetic_version: str | None = None,
        source: str | None = None,
        parent_id: int | None = None,
        metadata: str | None = None,
        pinned: bool = False,
        created_at: str | None = None,
        embed: bool = True,
        metadata_obj: dict[str, Any] | None = None,
    ) -> int:
        """記憶を1件追加する。

        embed=False のとき memory_vec に載せない（日記親など原文庫専用行）。
        created_at 未指定時は現在時刻（UTC）。
        metadata は JSON 文字列、metadata_obj は dict（どちらか一方）。
        """
        now = _utc_now_iso()
        created = created_at or now
        if metadata_obj is not None and metadata is not None:
            raise ValueError("metadata と metadata_obj は同時指定できない")
        meta = metadata
        if metadata_obj is not None:
            meta = json.dumps(metadata_obj, ensure_ascii=False)
        conn = self._connect()
        try:
            cursor = conn.execute(
                """
                INSERT INTO memories
                    (type, content, importance, sensitivity_grade, protection_grade,
                     cosmetic_version, created_at, last_accessed, access_count,
                     source, parent_id, metadata, pinned)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    type,
                    content,
                    importance,
                    sensitivity_grade,
                    protection_grade,
                    cosmetic_version,
                    created,
                    now,
                    source,
                    parent_id,
                    meta,
                    1 if pinned else 0,
                ),
            )
            memory_id = cursor.lastrowid
            if embed:
                vector = self._embedder.embed(content)
                conn.execute(
                    "INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                    (memory_id, sqlite_vec.serialize_float32(vector)),
                )
            conn.commit()
            return memory_id
        finally:
            conn.close()

    @overload
    def recall(self, query_text: str, top_k: int = 5, *, explain: bool = False) -> list[MemoryRecord]: ...

    @overload
    def recall(self, query_text: str, top_k: int, *, explain: bool) -> list[MemoryRecord]: ...

    def recall(self, query_text: str, top_k: int = 5, *, explain: bool = False) -> list[MemoryRecord]:
        """§4.4(2026-07-17改訂): 足し算の活性化モデルで想起する。

        活性化値 = 基礎活性（重要度＋鮮度＋保護等級A/Sの下駄）
                 ＋ 話題近接（ベクトル類似度・配点最大）
                 ＋ 連想伝播（一次発火の上位seedから意味的近傍へ1ホップ・減衰）
                 ＋ ゆらぎ（小乱数。noise_sigma=0で決定論）

        explain=True のとき MemoryRecord.explanation に部品別内訳を添付する（B5）。
        """
        p = self._recall_params
        query_vector = self._embedder.embed(query_text)
        now = datetime.now(timezone.utc)
        pool_k = max(top_k * 4, 20)
        base_plus_topic: dict[int, float] = {}  # ゆらぎ抜きの土台（話題近接まで）
        pooled_rows: dict[int, sqlite3.Row] = {}
        spread_bonus: dict[int, float] = {}  # seed横断でmax（後述: ハブ膨張防止）
        relevance_term: dict[int, float] = {}
        importance_term: dict[int, float] = {}
        recency_term: dict[int, float] = {}
        grade_bonus_term: dict[int, float] = {}

        conn = self._connect()
        try:
            for row in self._nearest_rows(conn, query_vector, pool_k):
                relevance = max(0.0, 1.0 - row["distance"])
                imp, rec, grade, _base = self._decompose_base_activation(row, now)
                importance_term[row["id"]] = imp
                recency_term[row["id"]] = rec
                grade_bonus_term[row["id"]] = grade
                relevance_term[row["id"]] = p.weight_relevance * relevance
                base_plus_topic[row["id"]] = _base + relevance_term[row["id"]]
                pooled_rows[row["id"]] = row

            # 連想伝播: 一次発火の上位seedから、意味的に近い記憶へ1ホップだけ活性を流す。
            # 複数seedからの伝播はmax（合算しない）。合算だと似た文面の記憶クラスタが
            # 何本もの伝播元から二重・三重に加算を受けて肥大化し（ハブ膨張）、話題と
            # 無関係でも密結合クラスタというだけで本命の正典を活性値で上回ってしまう
            # 実測不具合があったため（2026-07-17実測: 「約束」クラスタでヒット率9-55%
            # まで崩れた）、1ホップの効果は「最も強い1本の伝播経路」に限定する。
            seeds = sorted(base_plus_topic, key=base_plus_topic.__getitem__, reverse=True)[: p.spread_seeds]
            for seed_id in seeds:
                seed_vec = conn.execute(
                    "SELECT embedding FROM memory_vec WHERE memory_id = ?", (seed_id,)
                ).fetchone()
                if seed_vec is None:
                    continue
                for row in self._nearest_rows(conn, seed_vec["embedding"], pool_k):
                    if row["id"] == seed_id:
                        continue
                    similarity = max(0.0, 1.0 - row["distance"])
                    spread = p.spread_decay * p.weight_relevance * similarity
                    spread_bonus[row["id"]] = max(spread_bonus.get(row["id"], 0.0), spread)
                    if row["id"] not in base_plus_topic:
                        imp, rec, grade, base_only = self._decompose_base_activation(row, now)
                        importance_term[row["id"]] = imp
                        recency_term[row["id"]] = rec
                        grade_bonus_term[row["id"]] = grade
                        relevance_term[row["id"]] = 0.0
                        base_plus_topic[row["id"]] = base_only
                        pooled_rows[row["id"]] = row

            noise_by_id = {memory_id: self._noise() for memory_id in base_plus_topic}
            activation = {
                memory_id: (
                    value
                    + spread_bonus.get(memory_id, 0.0)
                    + noise_by_id[memory_id]
                )
                for memory_id, value in base_plus_topic.items()
            }
        finally:
            conn.close()

        surfaced: list[MemoryRecord] = []
        for memory_id, value in activation.items():
            if value < p.activation_floor:
                continue
            explanation = None
            if explain:
                explanation = RecallExplanation(
                    relevance=relevance_term.get(memory_id, 0.0),
                    importance=importance_term.get(memory_id, 0.0),
                    recency=recency_term.get(memory_id, 0.0),
                    grade_bonus=grade_bonus_term.get(memory_id, 0.0),
                    spread=spread_bonus.get(memory_id, 0.0),
                    noise=noise_by_id.get(memory_id, 0.0),
                    activation=value,
                )
            surfaced.append(
                MemoryRecord.from_row(
                    pooled_rows[memory_id],
                    score=value,
                    explanation=explanation,
                )
            )
        surfaced.sort(key=lambda r: r.score, reverse=True)
        top = surfaced[:top_k]

        if top:
            self._refresh_access(record_ids=[r.id for r in top], now=now)

        return top

    def _nearest_rows(self, conn: sqlite3.Connection, embedding, k: int) -> list[sqlite3.Row]:  # noqa: ANN001
        """ベクトル近傍の記憶行をdistance付きで返す。embeddingはfloat列またはシリアル済みblob。"""
        blob = embedding if isinstance(embedding, bytes) else sqlite_vec.serialize_float32(embedding)
        return conn.execute(
            """
            SELECT m.*, v.distance AS distance
            FROM memory_vec v
            JOIN memories m ON m.id = v.memory_id
            LEFT JOIN memory_tombstones t ON t.memory_id = m.id
            WHERE v.embedding MATCH ? AND k = ? AND t.memory_id IS NULL
            ORDER BY v.distance
            """,
            (blob, k),
        ).fetchall()

    def get_memory_by_id(self, memory_id: int) -> tuple[MemoryRecord, bool] | None:
        """記憶行と pinned フラグを返す。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return MemoryRecord.from_row(row), bool(row["pinned"])

    def is_pinned(self, memory_id: int) -> bool:
        pair = self.get_memory_by_id(memory_id)
        return bool(pair and pair[1])

    def is_memory_tombstoned(self, memory_id: int) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM memory_tombstones WHERE memory_id = ?",
                (memory_id,),
            ).fetchone()
        finally:
            conn.close()
        return row is not None

    def tombstone_memory(self, memory_id: int, *, reason: str) -> None:
        """記憶を tombstone し、ベクトル index から除外する。

        backup（B7）と控え・変更レポートは呼び出し側の責務（正規経路は directed_forget）。
        """
        now = _utc_now_iso()
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_tombstones (memory_id, tombstoned_at, reason)
                VALUES (?, ?, ?)
                """,
                (memory_id, now, reason),
            )
            conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (memory_id,))
            conn.commit()
        finally:
            conn.close()

    def physical_delete_memory(self, memory_id: int) -> None:
        """記憶を物理削除する（明示指示時のみ）。

        backup（B7）と控え・変更レポートは呼び出し側の責務（正規経路は directed_forget）。
        """
        conn = self._connect()
        try:
            conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memory_tombstones WHERE memory_id = ?", (memory_id,))
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            conn.commit()
        finally:
            conn.close()

    @property
    def facts(self) -> FactStore:
        """Fact 台帳（同一 DB）。

        2026-07-26 B2: facts_vecの次元はmemory_vecと同じ`self._vector_dim`に揃える
        （本番はbge-m3の1024次元、テストは軽量スタブに合わせた小さい次元）。
        """
        return FactStore(self._db_path, vector_dim=self._vector_dim)

    def _decompose_base_activation(
        self, row: sqlite3.Row, now: datetime,
    ) -> tuple[float, float, float, float]:
        """基礎活性を (importance, recency, grade_bonus, total) に分解する。"""
        p = self._recall_params
        recency_factor = 1.0 / (1.0 + _days_since(row["last_accessed"], now))
        importance = p.weight_importance * row["importance"]
        recency = p.weight_recency * recency_factor
        grade_bonus = 0.0
        if row["protection_grade"] == "S":
            grade_bonus = p.grade_bonus_s
        elif row["protection_grade"] == "A":
            grade_bonus = p.grade_bonus_a
        return importance, recency, grade_bonus, importance + recency + grade_bonus

    def _noise(self) -> float:
        if self._recall_params.noise_sigma <= 0.0:
            return 0.0
        return self._rng.gauss(0.0, self._recall_params.noise_sigma)

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
            rows = self._nearest_rows(conn, query_vector, 1)
        finally:
            conn.close()

        if not rows:
            return None
        row = rows[0]

        relevance = max(0.0, 1.0 - row["distance"])
        return MemoryRecord.from_row(row), relevance

    def get_unassessed_memories(
        self,
        *,
        exclude_protection_grade: str = "S",
        limit: int = 1,
        exclude_ids: set[int] | None = None,
    ) -> list[MemoryRecord]:
        """機微未査定の記憶を古い順に取得する（§4.6-3、Ollamaのアイドル仕事の入力）。

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
                    SELECT m.* FROM memories m
                    LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                    WHERE m.sensitivity_assessed = 0 AND m.protection_grade != ?
                      AND m.id NOT IN ({placeholders})
                      AND t.memory_id IS NULL
                    ORDER BY m.created_at ASC
                    LIMIT ?
                    """,
                    (exclude_protection_grade, *sorted(exclude_ids), limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT m.* FROM memories m
                    LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                    WHERE m.sensitivity_assessed = 0 AND m.protection_grade != ?
                      AND t.memory_id IS NULL
                    ORDER BY m.created_at ASC
                    LIMIT ?
                    """,
                    (exclude_protection_grade, limit),
                ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def list_memories_since(
        self,
        *,
        since_iso: str,
        until_iso: str | None = None,
        exclude_type: str | None = None,
    ) -> list[MemoryRecord]:
        """`since_iso`以降（`until_iso`指定時はその未満まで）に作られた記憶を古い順に返す
        （§4.5 日記材料: 当日の蒸留断片＋採用された記憶候補の取得に使う。§4.1「書き込みは
        このライン一本」のため両者は同じ集合）。

        `until_iso`は「Serina日ごとに1本」（§4.5）を保証するための上限。省略時（None）は
        従来通り無制限（呼び出し側が長期間分をまとめて取得したい場合の後方互換）。
        `exclude_type`は日記本文自体（type="diary"）を材料に混ぜて自己言及させないための除外。
        """
        conn = self._connect()
        try:
            conditions = ["m.created_at >= ?"]
            params: list[str] = [since_iso]
            if until_iso is not None:
                conditions.append("m.created_at < ?")
                params.append(until_iso)
            if exclude_type is not None:
                conditions.append("m.type != ?")
                params.append(exclude_type)
            where_clause = " AND ".join(conditions)
            rows = conn.execute(
                f"""
                SELECT m.* FROM memories m
                LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                WHERE {where_clause} AND t.memory_id IS NULL
                ORDER BY m.created_at ASC
                """,
                params,
            ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def list_by_type(self, type: str, *, limit: int = 200) -> list[MemoryRecord]:
        """指定typeの記憶を新しい順に返す（例: GUIのアルバム表示 type="episodic"）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.* FROM memories m
                LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                WHERE m.type = ? AND t.memory_id IS NULL
                ORDER BY m.created_at DESC
                LIMIT ?
                """,
                (type, limit),
            ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def list_by_type_and_legacy_type(
        self, type: str, *, legacy_type: str, limit: int = 200,
    ) -> list[MemoryRecord]:
        """指定typeかつ`metadata.legacy_type`が一致する記憶を新しい順に返す。

        2026-07-23のepisodic/semantic統合で`event/knowledge/promise/relationship`が
        全て`semantic`へ畳まれた際、元の分類を`metadata.legacy_type`に焼き込んで保持した
        （`tools/migrate_memory_types.py`）。`core.runtime.list_promise_memories_for_pulse()`
        のように「semanticの中から旧promiseだけ」を引く必要がある呼び出し元向け。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.* FROM memories m
                LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                WHERE m.type = ? AND t.memory_id IS NULL
                ORDER BY m.created_at DESC
                """,
                (type,),
            ).fetchall()
        finally:
            conn.close()
        matched: list[MemoryRecord] = []
        for row in rows:
            meta_raw = row["metadata"] if "metadata" in row.keys() else None
            try:
                meta = json.loads(meta_raw) if meta_raw else {}
            except json.JSONDecodeError:
                meta = {}
            if isinstance(meta, dict) and meta.get("legacy_type") == legacy_type:
                matched.append(MemoryRecord.from_row(row))
                if len(matched) >= limit:
                    break
        return matched

    _MAINT_GRADE_RANK = {"S": 0, "A": 1, "B": 2}

    def list_memories_for_maint(
        self,
        *,
        q: str = "",
        type: str = "",  # noqa: A002 — GUI/API語彙に合わせる（記憶のtype列）
        sort: str = "date",
        dir: str = "desc",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """メンテ用一覧。tombstone除外・content部分一致（空qは全件）・type絞り込み・列ソート。

        返却 dict: id / type / content / protection_grade / created_at / pinned / metadata。
        意味検索(recall)は使わず単純SQL。並び替えは件数が小さい前提でPython側（等級は
        S>A>B優先順・種別/日付/内容はいずれもコードポイント順の単純比較。内容の並びは
        真の50音順ではない点に注意）。総件数はページネーション用。
        """
        if limit < 1:
            raise ValueError("limit は1以上")
        if offset < 0:
            raise ValueError("offset は0以上")
        if sort not in ("date", "type", "grade", "content"):
            raise ValueError(f"sort が不正: {sort}")
        if dir not in ("asc", "desc"):
            raise ValueError(f"dir が不正: {dir}")
        needle = (q or "").strip()
        conn = self._connect()
        try:
            where = ["t.memory_id IS NULL"]
            params: list[Any] = []
            if needle:
                where.append("m.content LIKE ?")
                params.append(f"%{needle}%")
            if type:
                where.append("m.type = ?")
                params.append(type)
            where_sql = " AND ".join(where)
            rows = conn.execute(
                f"""
                SELECT m.id, m.type, m.content, m.protection_grade, m.created_at, m.pinned,
                       m.metadata
                FROM memories m
                LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                WHERE {where_sql}
                """,
                params,
            ).fetchall()
        finally:
            conn.close()

        from serina.core.memory.diary_date import parse_memory_metadata

        items = [
            {
                "id": row["id"],
                "type": row["type"],
                "content": row["content"],
                "protection_grade": row["protection_grade"],
                "created_at": row["created_at"],
                "pinned": bool(row["pinned"]),
                "metadata": parse_memory_metadata(row["metadata"]),
            }
            for row in rows
        ]

        if sort == "grade":
            key = lambda it: self._MAINT_GRADE_RANK.get(it["protection_grade"], 9)  # noqa: E731
        elif sort == "content":
            key = lambda it: (it["content"] or "")  # noqa: E731
        elif sort == "type":
            key = lambda it: (it["type"] or "")  # noqa: E731
        else:
            key = lambda it: (it["created_at"] or "")  # noqa: E731
        items.sort(key=key, reverse=(dir == "desc"))

        total = len(items)
        page = items[offset:offset + limit]
        return page, total

    def list_children(self, parent_id: int) -> list[MemoryRecord]:
        """parent_idが指定行を参照する子記憶を返す（レガシー投入時代の日記チャンク等）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT m.* FROM memories m
                LEFT JOIN memory_tombstones t ON t.memory_id = m.id
                WHERE t.memory_id IS NULL AND m.parent_id = ?
                ORDER BY m.id
                """,
                (parent_id,),
            ).fetchall()
        finally:
            conn.close()
        return [MemoryRecord.from_row(row) for row in rows]

    def update_memory_fields(
        self,
        memory_id: int,
        *,
        content: str | None = None,
        protection_grade: str | None = None,
    ) -> None:
        """GUIメンテ編集の実書き込み。content変更時は既存の埋め込みがある行のみ再生成する。

        保護3原則のガード（pinned禁止・S級確認・変更ログ・世代控え）は呼び出し元の
        core.memory.memory_edit が担う。ここは素のDB更新のみ。
        """
        if content is None and protection_grade is None:
            raise ValueError("content または protection_grade の少なくとも一方を指定すること")
        conn = self._connect()
        try:
            if content is not None:
                conn.execute(
                    "UPDATE memories SET content = ? WHERE id = ?", (content, memory_id),
                )
                had_vec = conn.execute(
                    "SELECT 1 FROM memory_vec WHERE memory_id = ?", (memory_id,),
                ).fetchone()
                if had_vec:
                    vector = self._embedder.embed(content)
                    conn.execute("DELETE FROM memory_vec WHERE memory_id = ?", (memory_id,))
                    conn.execute(
                        "INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                        (memory_id, sqlite_vec.serialize_float32(vector)),
                    )
            if protection_grade is not None:
                conn.execute(
                    "UPDATE memories SET protection_grade = ? WHERE id = ?",
                    (protection_grade, memory_id),
                )
            conn.commit()
        finally:
            conn.close()

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

    def has_distillation_key(self, content_hash: str) -> bool:
        """蒸留冪等キーが既に処理済みか（合意台帳 §4-1）。"""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM distillation_keys WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def record_distillation_key(self, content_hash: str) -> None:
        """蒸留冪等キーを記録する（再実行で二重記憶を作らない）。"""
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO distillation_keys (content_hash, processed_at)
                VALUES (?, ?)
                """,
                (content_hash, _utc_now_iso()),
            )
            conn.commit()
        finally:
            conn.close()

    def rebuild_vector_index(self) -> int:
        """memory_vec を memories 本文から全再構築する（合意台帳 §4-7）。

        vec0 は行更新が弱いため、全削除→再 INSERT する。本文 DB は無傷。
        親行（他行の parent_id から参照される id）は原文庫のため埋め込み対象外。
        戻り値は再埋め込みした件数。
        """
        conn = self._connect()
        try:
            rows = conn.execute(
                """
                SELECT id, content FROM memories
                WHERE id NOT IN (
                    SELECT parent_id FROM memories WHERE parent_id IS NOT NULL
                )
                ORDER BY id
                """
            ).fetchall()
            conn.execute("DELETE FROM memory_vec")
            count = 0
            for row in rows:
                vector = self._embedder.embed(row["content"])
                conn.execute(
                    "INSERT INTO memory_vec (memory_id, embedding) VALUES (?, ?)",
                    (row["id"], sqlite_vec.serialize_float32(vector)),
                )
                count += 1
            conn.commit()
            return count
        finally:
            conn.close()

    def rebuild_facts_vector_index(self) -> int:
        """facts_vec を facts.statement から全再構築する（埋め込みモデル差し替え時用）。

        memory_vec の rebuild_vector_index と対になる保守口。本文（facts）は無傷。
        """
        return self.facts.rebuild_embeddings(self._embedder.embed)


class MemoryWriteAPI:
    """書込系の薄い公開口。既存メソッドへの委譲のみ。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def add_memory(self, *args, **kwargs) -> int:  # noqa: ANN002, ANN003
        return self._store.add_memory(*args, **kwargs)

    def update_sensitivity(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return self._store.update_sensitivity(*args, **kwargs)

    def record_distillation_key(self, content_hash: str) -> None:
        return self._store.record_distillation_key(content_hash)

    def add_fact(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self._store.facts.add_fact(*args, **kwargs)

    def supersede_fact(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self._store.facts.supersede_fact(*args, **kwargs)

    def tombstone_fact(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return self._store.facts.tombstone_fact(*args, **kwargs)


class MemoryReadAPI:
    """読出系の薄い公開口。既存メソッドへの委譲のみ。"""

    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    def recall(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.recall(*args, **kwargs)

    def nearest_relevance(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.nearest_relevance(*args, **kwargs)

    def get_unassessed_memories(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.get_unassessed_memories(*args, **kwargs)

    def list_memories_since(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.list_memories_since(*args, **kwargs)

    def list_by_type(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.list_by_type(*args, **kwargs)

    def list_by_type_and_legacy_type(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.list_by_type_and_legacy_type(*args, **kwargs)

    def list_memories_for_maint(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return self._store.list_memories_for_maint(*args, **kwargs)

    def has_distillation_key(self, content_hash: str) -> bool:
        return self._store.has_distillation_key(content_hash)

    def get_fact(self, fact_id: str):
        return self._store.facts.get_fact(fact_id)

    def list_active_facts(self):
        return self._store.facts.list_active_facts()

    def embed_text(self, text: str) -> list[float]:
        return self._store.embed_text(text)
