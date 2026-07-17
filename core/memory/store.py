"""記憶DBアクセス層。設計書 §4.2, §4.4, §1.3(Memory=Coreの内臓)

新規実装（旧memory/store.py, memory/db.pyは参照しない）。
既存の物理スキーマ（memoriesテーブル・memory_vec vec0仮想テーブル）は継承資産として踏襲する。
_ensure_schema()のCREATE TABLE文は実DBの物理スキーマ（tools/migrate_memory_schema.py適用後）と一致させてある。
想起: 足し算の活性化モデル1本（基礎活性＋話題近接＋連想伝播＋ゆらぎ。§4.4 2026-07-17改訂）。
旧二経路（かけ算スコア＋trigger_keywordsトリガー想起）は撤去済み。控えはコミット7aba434。
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

import sqlite_vec

from serina.core.memory.embedder import OllamaEmbedder

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
        """§4.4(2026-07-17改訂): 足し算の活性化モデルで想起する。

        活性化値 = 基礎活性（重要度＋鮮度＋保護等級A/Sの下駄）
                 ＋ 話題近接（ベクトル類似度・配点最大）
                 ＋ 連想伝播（一次発火の上位seedから意味的近傍へ1ホップ・減衰）
                 ＋ ゆらぎ（小乱数。noise_sigma=0で決定論）

        記憶＝ノード・想起＝発火・連想＝活性の伝播、という疑似ニューラルネットワークの
        思想（§4.4設計思想）。activation_floor未満は件数枠が余っても浮上させない。
        約束・正典級の100%保証は§4.4改訂で撤廃済み（目標: 実測ヒット率90%以上）。
        """
        p = self._recall_params
        query_vector = self._embedder.embed(query_text)
        now = datetime.now(timezone.utc)
        pool_k = max(top_k * 4, 20)
        base_plus_topic: dict[int, float] = {}  # ゆらぎ抜きの土台（話題近接まで）
        pooled_rows: dict[int, sqlite3.Row] = {}
        spread_bonus: dict[int, float] = {}  # seed横断でmax（後述: ハブ膨張防止）

        conn = self._connect()
        try:
            for row in self._nearest_rows(conn, query_vector, pool_k):
                relevance = max(0.0, 1.0 - row["distance"])
                base_plus_topic[row["id"]] = (
                    self._base_activation(row, now) + p.weight_relevance * relevance
                )
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
                        base_plus_topic[row["id"]] = self._base_activation(row, now)
                        pooled_rows[row["id"]] = row

            activation = {
                memory_id: value + spread_bonus.get(memory_id, 0.0) + self._noise()
                for memory_id, value in base_plus_topic.items()
            }
        finally:
            conn.close()

        surfaced = [
            MemoryRecord.from_row(pooled_rows[memory_id], score=value)
            for memory_id, value in activation.items()
            if value >= p.activation_floor
        ]
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
            WHERE v.embedding MATCH ? AND k = ?
            ORDER BY v.distance
            """,
            (blob, k),
        ).fetchall()

    def _base_activation(self, row: sqlite3.Row, now: datetime) -> float:
        """基礎活性: 重要度＋鮮度＋保護等級A/Sの下駄（§4.4付帯ルール1）。"""
        p = self._recall_params
        recency = 1.0 / (1.0 + _days_since(row["last_accessed"], now))
        base = p.weight_importance * row["importance"] + p.weight_recency * recency
        if row["protection_grade"] == "S":
            base += p.grade_bonus_s
        elif row["protection_grade"] == "A":
            base += p.grade_bonus_a
        return base

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
