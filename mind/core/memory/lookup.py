"""記録を確かめる道。会話の原文が正本、FTSは眠りで作り直す写し。"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mind.core.lifelog import MASTER
from mind.core.memory.index import normalize
from mind.core.memory.recall import Cue, Recaller
from mind.core.memory.time_cue import read_period

JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class Record:
    ref: str
    ts: datetime
    speaker: str
    text: str

    def format(self) -> str:
        who = "マスター" if self.speaker.casefold() == MASTER.casefold() else "セリナ"
        # 原文を別行に置く。家の印は文の指紋なので、日時・話者を接いで指紋を変えてはならない。
        return f"{self.ts.astimezone(JST):%Y-%m-%d} {who}:\n{self.text}"


def _live_today(directory: Path, now: datetime) -> dict[str, Record]:
    """索引更新前の今日の会話。消された行は番号だけ数えて絶対に返さない。"""
    day = now.astimezone(JST).date().isoformat()
    path = directory / f"{day}.jsonl"
    result = {}
    if not path.exists():
        return result
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            try:
                row = json.loads(raw)
                if row.get("deleted") or not row.get("text"):
                    continue
                result[f"{day}#{n}"] = Record(
                    f"{day}#{n}", datetime.fromisoformat(row["ts"]), row["speaker"], row["text"]
                )
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
    return result


def _terms(cue: Cue, recaller: Recaller) -> list[str]:
    text = cue.utterance
    concepts = recaller.concepts_in(text)
    terms = set(recaller.terms_in(text, concepts))
    # 「覚えてる？」だけでも直前の会話に具体的な話題があればその語を使う。
    if not concepts and not terms and cue.recent:
        for _, text in reversed(cue.recent[-recaller.params.recent_turns:]):
            concepts = recaller.concepts_in(text)
            terms = set(recaller.terms_in(text, concepts))
            if concepts or terms:
                break
    # 同じ概念の呼び名も引く。索引の言葉の手がかりと辞書を再利用する。
    terms.update(normalize(surface) for surface, concept in recaller.index.surfaces.items() if concept in concepts)
    terms.update(normalize(concept) for concept in concepts)
    return sorted((word for word in terms if len(word) >= 2), key=lambda x: (-len(x), x))[:12]


def _rows_for(db: sqlite3.Connection, term: str) -> list[Record]:
    if len(term) >= 3:
        # MATCHの入力は必ず引用符で囲む。引用符もエスケープして句として検索する。
        query = '"' + term.replace('"', '""') + '"'
        sql = "SELECT ref, ts, speaker, text FROM record_fts WHERE search_text MATCH ?"
        args = (query,)
    else:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql = "SELECT ref, ts, speaker, text FROM record_fts WHERE search_text LIKE ? ESCAPE '\\'"
        args = (f"%{escaped}%",)
    return [Record(ref, datetime.fromisoformat(ts), speaker, body) for ref, ts, speaker, body in db.execute(sql, args)]


def _still_present(directory: Path, record: Record) -> bool:
    """削除と索引再生成が競合しても、消した発言を渡さない。"""
    day, number = record.ref.split("#", 1)
    path = directory / f"{day}.jsonl"
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if n == int(number):
                try:
                    row = json.loads(raw)
                    return not row.get("deleted") and row.get("text") == record.text
                except (ValueError, AttributeError):
                    return False
    return False


class RecordLookup:
    def __init__(self, index_path: Path, conversation_dir: Path):
        self.index_path = index_path
        self.conversation_dir = conversation_dir

    def search(
        self, cue: Cue, recaller: Recaller, *, limit: int, neighbors: int = 1
    ) -> tuple[list[Record], set[str]]:
        """珍しさの和でヒットを選び、当たった行の前後を添える。ページidはヒット行だけから計算。"""
        words = _terms(cue, recaller)
        if not words or not self.index_path.exists() or limit <= 0:
            return [], set()
        period = read_period(
            cue.utterance, cue.now.astimezone(JST),
            earliest=recaller._first.astimezone(JST).date() if recaller._first else None,
        )
        rows: dict[str, Record] = {}
        matched: dict[str, set[str]] = defaultdict(set)
        sources: dict[str, set[str]] = defaultdict(set)
        live = _live_today(self.conversation_dir, cue.now)
        try:
            with closing(sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)) as db:
                # 古い版の索引なら失敗を外へ出さず、次のrebuild_indexを待つ。
                total = db.execute("SELECT count(*) FROM record_fts").fetchone()[0]
                for word in words:
                    for record in _rows_for(db, word):
                        rows[record.ref] = record
                        matched[record.ref].add(word)
                # 眠る前の新しい発言にも同じ検索条件を適用する。
                for record in live.values():
                    for word in words:
                        if word in normalize(record.text):
                            rows[record.ref] = record
                            matched[record.ref].add(word)
                if not matched:
                    return [], set()
                per_word = {word: sum(word in found for found in matched.values()) for word in words}
                indexed_today = {
                    ref for (ref,) in db.execute(
                        "SELECT ref FROM record_fts WHERE ref LIKE ?",
                        (cue.now.astimezone(JST).date().isoformat() + "#%",),
                    )
                }
                total += len(set(live) - indexed_today)
                scores = {
                    ref: sum(math.log(max(total, 1) / max(per_word[word], 1)) for word in found)
                    for ref, found in matched.items()
                }
                hits = sorted(
                    (rows[ref] for ref in matched if (period is None or period.contains(rows[ref].ts.astimezone(JST).date()))
                     and _still_present(self.conversation_dir, rows[ref])),
                    key=lambda record: (-scores[record.ref], record.ts, record.ref),
                )[:max(1, limit // (2 * neighbors + 1))]
                if not hits:
                    return [], set()
                hit_refs = tuple(hit.ref for hit in hits)
                slots = ",".join("?" for _ in hit_refs)
                for ref, page in db.execute(
                    f"SELECT ref, page_id FROM page_sources WHERE ref IN ({slots})", hit_refs
                ):
                    sources[ref].add(page)
                # ヒットの周囲を原文の範囲内で補い、削除済みの行は混ぜない。
                neighbors_by_ref: dict[str, Record] = {}
                selected = {hit.ref for hit in hits}
                for hit in hits:
                    day, number = hit.ref.split("#", 1)
                    for i in range(max(1, int(number) - neighbors), int(number) + neighbors + 1):
                        ref = f"{day}#{i}"
                        if ref in live:
                            record = live[ref]
                        else:
                            row = db.execute(
                                "SELECT ref, ts, speaker, text FROM record_fts WHERE ref = ?", (ref,)
                            ).fetchone()
                            record = Record(row[0], datetime.fromisoformat(row[1]), row[2], row[3]) if row else None
                        if record and _still_present(self.conversation_dir, record):
                            neighbors_by_ref[ref] = record
                # 行数上限を破らず、まず当たった行を優先する。
                kept = {record.ref: record for record in hits}
                for ref, record in sorted(neighbors_by_ref.items()):
                    if len(kept) >= limit:
                        break
                    kept[ref] = record
                ordered = sorted(kept.values(), key=lambda record: (record.ts, record.ref))
                return ordered, {page for hit in hits for page in sources.get(hit.ref, ())}
        except (sqlite3.DatabaseError, OSError, ValueError):
            return [], set()

    def evidence(self, page_id: str, cue: Cue, recaller: Recaller, *, limit: int) -> list[Record]:
        """はっきり浮かんだページのsourceから原文を選ぶ。意味比較は想起済みのベクトルを再利用。"""
        if not self.index_path.exists() or limit <= 0:
            return []
        try:
            with closing(sqlite3.connect(f"file:{self.index_path.as_posix()}?mode=ro", uri=True)) as db:
                refs = [ref for (ref,) in db.execute(
                    "SELECT ref FROM page_sources WHERE page_id=?", (page_id,)
                )]
                if not refs:
                    return []
                passages = [
                    passage for passage in recaller.index.passages
                    if passage.page_id == page_id and passage.role == "record"
                ]
                vectors = getattr(recaller, "_cue_vectors", ())
                records = []
                for ref in refs:
                    row = db.execute(
                        "SELECT ref, ts, speaker, text FROM record_fts WHERE ref = ?", (ref,)
                    ).fetchone()
                    if row:
                        record = Record(row[0], datetime.fromisoformat(row[1]), row[2], row[3])
                        if _still_present(self.conversation_dir, record):
                            records.append(record)
                # 記録の1発言が500字を超えても、切れ端と重なる箇所から採点する。
                # 既存の索引ベクトルと想起済みベクトルだけを使い、埋め込み直さない。
                def overlaps(original: str, passage: str) -> bool:
                    left, right = normalize(original), normalize(passage)
                    width = min(24, len(left))
                    if width == 0:
                        return False
                    return any(
                        left[i:i + width] in right
                        for i in range(0, len(left) - width + 1, max(1, width // 2))
                    ) or left[-width:] in right

                def score(record: Record) -> tuple[float, datetime]:
                    candidates = [p for p in passages if overlaps(record.text, p.text)]
                    semantic = max(
                        (sum(a * b for a, b in zip(v, p.vector))
                         for p in candidates for v in vectors),
                        default=-2.0,
                    )
                    return semantic, record.ts

                return sorted(records, key=score, reverse=True)[:limit]
        except (sqlite3.DatabaseError, OSError, ValueError):
            return []
