"""記憶の索引（イデアの data/memory_index.db）。docs/plans/長期記憶の作り直し.md §3。

memory/ のページ・呼び名の辞書（memory/concepts.toml）・lifelog の記録から、いつでも作り直せる。壊れても、消して作り直せばよい。
持つもの：ページの中身と、そのページが拠っている記録の原文、概念のつながり、意味の近さを測るための埋め込み（bge-m3）。
埋め込みは文の指紋で控えておくので、作り直しても、変わっていない文は埋め込み直さない。

思い出すとき（recall.py）は、索引をまるごと手元に読み込んで使う（ページは数百から数千で、手元に収まる）。
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import struct
import tomllib
import unicodedata
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mind.core.lifelog import read_conversation
from mind.core.memory.legacy_parse import strip_ornament
from mind.core.memory.page import Page, load_pages
from mind.core.memory.structure import MASTER, MASTER_NAME, episode_evidence, span_text

PASSAGE_CHARS = 500  # 意味の近さを測る一切れの長さ
CONCEPTS_FILE = "concepts.toml"

_SCHEMA = """
CREATE TABLE pages (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, start TEXT, "end" TEXT,
    title TEXT, gist TEXT, body TEXT, evidence TEXT, importance INTEGER, arousal REAL,
    prev TEXT, next TEXT, written INTEGER NOT NULL
);
CREATE TABLE page_concepts (page_id TEXT NOT NULL, concept TEXT NOT NULL);
CREATE TABLE aliases (surface TEXT PRIMARY KEY, concept TEXT NOT NULL);
CREATE TABLE passages (page_id TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL, vector BLOB NOT NULL);
CREATE TABLE embeddings (key TEXT PRIMARY KEY, vector BLOB NOT NULL);
"""


def normalize(text: str) -> str:
    """照らし合わせるための形。全角半角と大文字小文字をそろえ、空白を落とす。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).lower()


def load_aliases(memory_dir: Path) -> dict[str, str]:
    """呼び名（正規化した形）→ 代表の概念。代表の名前そのものも含める。"""
    path = Path(memory_dir) / CONCEPTS_FILE
    if not path.exists():
        return {}
    raw = tomllib.loads(path.read_text(encoding="utf-8")).get("concept", {})
    out: dict[str, str] = {}
    for concept, entry in raw.items():
        out[normalize(concept)] = concept
        for alias in entry.get("aliases", []):
            out[normalize(alias)] = concept
    return out


def canonical(concept: str, aliases: dict[str, str]) -> str:
    return aliases.get(normalize(concept), concept)


def _pack(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _chunks(text: str, limit: int = PASSAGE_CHARS) -> list[str]:
    """段落（なければ行）を、limit 字ほどの一切れにまとめる。長すぎる段落は切る。"""
    pieces = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    out: list[str] = []
    buf = ""
    for piece in pieces:
        while len(piece) > limit:
            if buf:
                out.append(buf)
                buf = ""
            out.append(piece[:limit])
            piece = piece[limit:]
        if buf and len(buf) + len(piece) + 1 > limit:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n{piece}" if buf else piece
    if buf:
        out.append(buf)
    return out


def _evidence(page: Page, lines, labels: dict[str, str]) -> str:  # noqa: ANN001
    if page.kind == "episode":
        return span_text(episode_evidence(lines, page, labels), labels)
    return page.body  # 日記と覚え書きは、本文が記録の写しそのもの


def _passages(page: Page, evidence: str) -> list[tuple[str, str]]:
    """(役割, 文)。head：題と要点、body：本人の文、record：記録の原文。"""
    out: list[tuple[str, str]] = []
    head = "。".join(x for x in (page.title, page.gist) if x)
    if head:
        out.append(("head", head))
    if page.kind == "episode":
        if page.body:
            out += [("body", chunk) for chunk in _chunks(page.body)]
        out += [("record", chunk) for chunk in _chunks(evidence)]
    else:
        out += [("body", chunk) for chunk in _chunks(page.body)]
    return out


def build_index(
    memory_dir: Path,
    conversation_dir: Path,
    resident: str,
    target: Path,
    *,
    embed: Callable[[str], list[float]],
    model: str,
    progress: Callable[[str], None] = print,
) -> int:
    """索引を作り直す。一時ファイルに作ってから置き換える。作ったページの数を返す。"""
    pages = load_pages(memory_dir)
    aliases = load_aliases(memory_dir)
    labels = {MASTER: MASTER_NAME, resident: "わたし"}
    lines = read_conversation(conversation_dir)
    known: dict[str, bytes] = {}
    if target.exists():
        with closing(sqlite3.connect(target)) as old:
            try:
                known = dict(old.execute("SELECT key, vector FROM embeddings"))
            except sqlite3.DatabaseError:
                known = {}
    tmp = target.with_suffix(".db.tmp")
    tmp.unlink(missing_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    embedded = 0
    with closing(sqlite3.connect(tmp)) as db:
        db.executescript(_SCHEMA)
        for n, page in enumerate(pages, start=1):
            evidence = _evidence(page, lines, labels)
            db.execute(
                'INSERT INTO pages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (
                    page.id, page.kind,
                    page.start.isoformat() if page.start else None, page.end.isoformat() if page.end else None,
                    page.title, page.gist, page.body, evidence, page.importance, page.arousal,
                    page.prev, page.next, int(page.written),
                ),
            )
            concepts = dict.fromkeys(canonical(c, aliases) for c in page.concepts)
            db.executemany("INSERT INTO page_concepts VALUES (?,?)", [(page.id, c) for c in concepts])
            for role, text in _passages(page, evidence):
                key = hashlib.sha256(f"{model}\0{text}".encode("utf-8")).hexdigest()
                if key not in known:
                    known[key] = _pack(embed(text))
                    embedded += 1
                db.execute("INSERT OR IGNORE INTO embeddings VALUES (?,?)", (key, known[key]))
                db.execute("INSERT INTO passages VALUES (?,?,?,?)", (page.id, role, text, known[key]))
            if n % 20 == 0:
                progress(f"  索引 {n}/{len(pages)}（新しく埋め込んだ文 {embedded}）")
        db.executemany("INSERT INTO aliases VALUES (?,?)", list(aliases.items()))
        db.commit()
    os.replace(tmp, target)
    progress(f"索引を作った: {len(pages)}ページ（新しく埋め込んだ文 {embedded}）")
    return len(pages)


@dataclass(frozen=True)
class IndexedPage:
    id: str
    kind: str
    start: datetime | None
    end: datetime | None
    title: str
    gist: str
    body: str
    evidence: str
    importance: int | None
    arousal: float | None  # そのときの高ぶり（分からなければ None。順位では真ん中）
    prev: str
    next: str
    written: bool
    concepts: frozenset[str]
    searchable: str  # 題・要点・本文・記録の原文を正規化してつないだもの（言葉の一致を探す）


@dataclass(frozen=True)
class Passage:
    page_id: str
    role: str
    text: str
    vector: tuple[float, ...]  # 長さ1にそろえてある


class MemoryIndex:
    """索引をまるごと手元に読み込んだもの。"""

    def __init__(self, pages: dict[str, IndexedPage], passages: list[Passage], aliases: dict[str, str]) -> None:
        self.pages = pages
        self.passages = passages
        self.aliases = aliases
        self.fan: dict[str, int] = {}
        for page in pages.values():
            for concept in page.concepts:
                self.fan[concept] = self.fan.get(concept, 0) + 1
        # 会話に出てきたら、その概念のことだと分かる呼び方（正規化した形 → 代表の概念）
        self.surfaces: dict[str, str] = {normalize(concept): concept for concept in self.fan} | aliases
        self._df: dict[str, int] = {}

    @classmethod
    def load(cls, path: Path) -> MemoryIndex:
        with closing(sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)) as db:
            concepts: dict[str, set[str]] = {}
            for page_id, concept in db.execute("SELECT page_id, concept FROM page_concepts"):
                concepts.setdefault(page_id, set()).add(concept)
            pages = {}
            for row in db.execute(
                'SELECT id, kind, start, "end", title, gist, body, evidence, importance, arousal, prev, next, written FROM pages'
            ):
                pid, kind, start, end, title, gist, body, evidence, importance, arousal, prev, nxt, written = row
                pages[pid] = IndexedPage(
                    id=pid, kind=kind,
                    start=datetime.fromisoformat(start) if start else None,
                    end=datetime.fromisoformat(end) if end else None,
                    title=title or "", gist=gist or "", body=body or "", evidence=evidence or "",
                    importance=importance, arousal=arousal, prev=prev or "", next=nxt or "",
                    written=bool(written), concepts=frozenset(concepts.get(pid, ())),
                    searchable=normalize(strip_ornament("\n".join((title or "", gist or "", body or "", evidence or "")))),
                )
            passages = []
            for page_id, role, text, blob in db.execute("SELECT page_id, role, text, vector FROM passages"):
                vector = _unpack(blob)
                norm = math.sqrt(sum(x * x for x in vector)) or 1.0
                passages.append(Passage(page_id, role, text, tuple(x / norm for x in vector)))
            aliases = dict(db.execute("SELECT surface, concept FROM aliases"))
        return cls(pages, passages, aliases)

    def document_frequency(self, term: str) -> int:
        """その言葉（正規化した形）を含むページの数。"""
        if term not in self._df:
            self._df[term] = sum(1 for page in self.pages.values() if term in page.searchable)
        return self._df[term]
