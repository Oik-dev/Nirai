"""今の記憶（記憶DB serina_memory.db。2026-10時点の仕組み）を、テストの口で答えさせる。

会話のたびに精神がしていること（core/runtime.py の _recall_with_planner と _build_pack の想起の部分）を、同じ順で呼ぶ。
想起の計画（RecallPlanner。規則で決まらない発言はGemmaに相談）→ 今の一言と計画の問いで想起 → 時間つき事実 →
日記の切れ端を前後へ広げる → 同じ日の日記を添える。ツマミは本番と同じ config/thresholds.toml。

記憶DBは写しを渡すこと。想起による鮮度の書き戻しは止める（問題の順番で結果が変わらないように）。
ゆらぎは問題ごとに種を決めて、同じ問題には同じ答えを返す。
now より後に作られた記憶は、その問題の間だけ墓標を立てて想起から外す。
新しい記憶に切り替えたら（計画のM4）、このファイルは消す。
"""

from __future__ import annotations

import random
import sqlite3
from collections.abc import Callable
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.config import load_thresholds
from mind.core.context.recall_diary_link import expand_semantic_with_diary
from mind.core.context.recall_neighbors import expand_recall_neighbors
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.recall_planner import merge_memory_recalls, plan_recall, resolve_facts_for_plan
from mind.core.memory.store import VECTOR_DIM_DEFAULT, MemoryRecord, MemoryStore, RecallParams
from mind.memory_test.memory import Cue, Recalled

JST = ZoneInfo("Asia/Tokyo")
RECALL_TOP_K = 5  # core/runtime.py の RECALL_TOP_K と同じ
SESSION_TAIL_TURNS = 4  # core/runtime.py の _session_tail_text と同じ
HIDE_REASON = "memory_test:after_now"


def _parse(ts: str) -> datetime:
    at = datetime.fromisoformat(ts)
    return at if at.tzinfo else at.replace(tzinfo=JST)


class LegacyMemory:
    name = "legacy"

    def __init__(
        self,
        db_copy: Path,
        *,
        planner_judge: Callable[[str], dict] | None,
        embedder: OllamaEmbedder | None = None,
        vector_dim: int = VECTOR_DIM_DEFAULT,
    ) -> None:
        t = load_thresholds()
        self._db = db_copy
        self._store = MemoryStore(
            str(db_copy),
            embedder=embedder or OllamaEmbedder(request_timeout_seconds=t.embedder_request_timeout_seconds),
            vector_dim=vector_dim,
            recall_params=RecallParams(
                weight_relevance=t.recall_weight_relevance,
                weight_importance=t.recall_weight_importance,
                weight_recency=t.recall_weight_recency,
                grade_bonus_s=t.recall_grade_bonus_s,
                grade_bonus_a=t.recall_grade_bonus_a,
                spread_decay=t.recall_spread_decay,
                spread_seeds=t.recall_spread_seeds,
                noise_sigma=t.recall_noise_sigma,
                activation_floor=t.recall_activation_floor,
            ),
            rng=random.Random(0),
        )
        self._store._refresh_access = lambda **_: None  # noqa: SLF001 — 写しでも、問題どうしを独立させる
        self._planner_judge = planner_judge
        with closing(sqlite3.connect(db_copy)) as conn:
            self._created = {row[0]: _parse(row[1]) for row in conn.execute("SELECT id, created_at FROM memories")}
        self._hidden: frozenset[int] = frozenset()

    def recall(self, cue: Cue) -> list[Recalled]:
        self._store._rng.seed(f"{cue.now.isoformat()}\0{cue.utterance}")  # noqa: SLF001
        self._hide_after(cue.now)
        store = self._store
        plan = plan_recall(
            cue.utterance,
            session_tail="\n".join(f"{turn.speaker}: {turn.text}" for turn in cue.recent[-SESSION_TAIL_TURNS:]),
            now=cue.now,
            judge=self._planner_judge,
        )
        primary = store.recall(cue.utterance, top_k=RECALL_TOP_K)
        extras = [store.recall(q.q, top_k=RECALL_TOP_K) for q in plan.queries if q.type == "semantic"]
        memories = merge_memory_recalls(primary, *extras, top_k=RECALL_TOP_K)
        facts = resolve_facts_for_plan(plan, store.facts)
        if memories:
            memories = expand_recall_neighbors(store, list(memories))
            memories = expand_semantic_with_diary(store, list(memories))
        recalled = [
            Recalled(text=m.content, when=self._when(m))
            for m in memories
            if self._created.get(m.id, cue.now) <= cue.now
        ]
        recalled += [
            Recalled(text=f.statement, when=_parse(f.valid_from).astimezone(JST).date())
            for f in facts
            if _parse(f.recorded_at) <= cue.now
        ]
        return recalled

    def _hide_after(self, now: datetime) -> None:
        hidden = frozenset(memory_id for memory_id, created in self._created.items() if created > now)
        if hidden == self._hidden:
            return
        with closing(sqlite3.connect(self._db)) as conn, conn:
            conn.execute("DELETE FROM memory_tombstones WHERE reason = ?", (HIDE_REASON,))
            conn.executemany(
                "INSERT INTO memory_tombstones (memory_id, tombstoned_at, reason) VALUES (?, ?, ?)",
                [(memory_id, now.isoformat(), HIDE_REASON) for memory_id in hidden],
            )
        self._hidden = hidden

    @staticmethod
    def _when(m: MemoryRecord) -> date | None:
        day = (m.metadata or {}).get("date") or (m.metadata or {}).get("target_date")
        if day:
            return date.fromisoformat(str(day)[:10])
        if m.source and m.source.startswith("継承記憶"):
            return None  # 継承記憶の作成日は仮の値（2025-03-01）
        return _parse(m.created_at).astimezone(JST).date()
