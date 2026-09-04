from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3


class WorldMemoryRetrieverError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorldMemoryHit:
    episode_id: str
    session_id: str
    path: str
    excerpt: str
    rank: float

    def to_context(self) -> dict[str, object]:
        return {
            "episode_id": self.episode_id,
            "session_id": self.session_id,
            "path": self.path,
            "excerpt": self.excerpt,
        }


class WorldMemoryRetriever:
    """Derived local search index for public World Memory episodes.

    `world_memory/episodes` remains the source of truth. The SQLite database is
    only a disposable index and is synchronized lazily before every search.
    Private Memory paths are never scanned by this service.
    """

    DEFAULT_TOP_K = 4
    MAX_TOP_K = 10
    _INDEX_NAME = "world_memory.sqlite3"
    _SOURCE_TABLE = "episode_sources"
    _FTS_TABLE = "episodes_fts"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.episodes_root = self.root / "world_memory" / "episodes"
        self.index_root = self.root / "world_memory" / "index"
        self.db_path = self.index_root / self._INDEX_NAME

    def rebuild(self) -> int:
        """Delete the derived index and rebuild it from public episode files."""
        try:
            self.index_root.mkdir(parents=True, exist_ok=True)
            self.db_path.unlink(missing_ok=True)
            with closing(sqlite3.connect(self.db_path)) as connection:
                tokenizer = self._ensure_schema(connection)
                count = self._sync_sources(connection, tokenizer=tokenizer, force=True)
                connection.commit()
                return count
        except (OSError, sqlite3.Error) as exc:
            raise WorldMemoryRetrieverError("World Memory index rebuild failed") from exc

    def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        exclude_session_id: str | None = None,
        exclude_entry_markers: set[str] | None = None,
    ) -> list[WorldMemoryHit]:
        cleaned_query = " ".join(query.split()).strip()
        if not cleaned_query:
            return []
        bounded_top_k = max(1, min(int(top_k), self.MAX_TOP_K))
        try:
            return self._search_once(
                cleaned_query,
                top_k=bounded_top_k,
                exclude_session_id=exclude_session_id,
                exclude_entry_markers=exclude_entry_markers,
            )
        except (OSError, sqlite3.DatabaseError) as first_error:
            # The index is disposable. One rebuild attempt is safer than making
            # ordinary conversation fail because a derived DB was deleted or
            # corrupted.
            try:
                self.rebuild()
                return self._search_once(
                    cleaned_query,
                    top_k=bounded_top_k,
                    exclude_session_id=exclude_session_id,
                    exclude_entry_markers=exclude_entry_markers,
                )
            except (OSError, sqlite3.Error, WorldMemoryRetrieverError) as exc:
                raise WorldMemoryRetrieverError("World Memory retrieval failed") from exc
            finally:
                _ = first_error

    def _search_once(
        self,
        query: str,
        *,
        top_k: int,
        exclude_session_id: str | None,
        exclude_entry_markers: set[str] | None,
    ) -> list[WorldMemoryHit]:
        self.index_root.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            tokenizer = self._ensure_schema(connection)
            self._sync_sources(connection, tokenizer=tokenizer)
            query_terms = self._logical_terms(query)
            if not query_terms:
                return []
            fts_terms = self._fts_terms(query_terms, tokenizer)
            match_query = " OR ".join(f'"{term}"' for term in fts_terms)
            candidate_limit = max(20, top_k * 6)
            parameters: list[object] = [match_query]
            session_filter = ""
            if exclude_session_id:
                session_filter = " AND session_id != ?"
                parameters.append(exclude_session_id)
            parameters.append(candidate_limit)
            rows = connection.execute(
                f"""
                SELECT episode_id, session_id, path, content, bm25({self._FTS_TABLE}) AS rank
                FROM {self._FTS_TABLE}
                WHERE {self._FTS_TABLE} MATCH ?{session_filter}
                ORDER BY rank
                LIMIT ?
                """,
                parameters,
            ).fetchall()

        query_term_set = set(query_terms)
        min_overlap = 1
        minimum_coverage = 0.34 if len(query_terms) <= 3 else 0.20
        hits: list[WorldMemoryHit] = []
        for episode_id, session_id, path, content, rank in rows:
            if not all(isinstance(value, str) for value in (episode_id, session_id, path, content)):
                continue
            filtered_content = self._exclude_marked_entries(
                content,
                exclude_entry_markers or set(),
            )
            content_terms = set(self._logical_terms(self._content_for_search(filtered_content), max_terms=None))
            overlap = len(query_term_set.intersection(content_terms))
            coverage = overlap / max(1, len(query_term_set))
            if overlap < min_overlap or coverage < minimum_coverage:
                continue
            hits.append(
                WorldMemoryHit(
                    episode_id=episode_id,
                    session_id=session_id,
                    path=path,
                    excerpt=self._episode_excerpt(filtered_content, query_term_set),
                    rank=float(rank),
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def _ensure_schema(self, connection: sqlite3.Connection) -> str:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS retriever_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self._SOURCE_TABLE} (
                path TEXT PRIMARY KEY,
                episode_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL,
                size INTEGER NOT NULL
            )
            """
        )
        existing = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (self._FTS_TABLE,),
        ).fetchone()
        format_row = connection.execute(
            "SELECT value FROM retriever_meta WHERE key='index_format'"
        ).fetchone()
        if existing is not None and (format_row is None or format_row[0] != 'ngram-v3'):
            connection.execute(f"DROP TABLE {self._FTS_TABLE}")
            connection.execute(f"DELETE FROM {self._SOURCE_TABLE}")
            connection.execute("DELETE FROM retriever_meta WHERE key IN ('tokenizer', 'index_format')")
            existing = None
        if existing is not None:
            columns = {
                str(row[1])
                for row in connection.execute(f"PRAGMA table_info({self._FTS_TABLE})").fetchall()
            }
            if "search_text" not in columns:
                connection.execute(f"DROP TABLE {self._FTS_TABLE}")
                connection.execute(f"DELETE FROM {self._SOURCE_TABLE}")
                connection.execute("DELETE FROM retriever_meta WHERE key='tokenizer'")
                existing = None

        if existing is None:
            tokenizer = "trigram"
            try:
                connection.execute(
                    f"""
                    CREATE VIRTUAL TABLE {self._FTS_TABLE} USING fts5(
                        episode_id UNINDEXED,
                        session_id UNINDEXED,
                        path UNINDEXED,
                        content UNINDEXED,
                        search_text,
                        tokenize='trigram'
                    )
                    """
                )
            except sqlite3.OperationalError:
                tokenizer = "unicode61"
                connection.execute(
                    f"""
                    CREATE VIRTUAL TABLE {self._FTS_TABLE} USING fts5(
                        episode_id UNINDEXED,
                        session_id UNINDEXED,
                        path UNINDEXED,
                        content UNINDEXED,
                        search_text,
                        tokenize='unicode61'
                    )
                    """
                )
            connection.execute(
                "INSERT OR REPLACE INTO retriever_meta(key, value) VALUES('tokenizer', ?)",
                (tokenizer,),
            )
            connection.execute(
                "INSERT OR REPLACE INTO retriever_meta(key, value) VALUES('index_format', 'ngram-v3')"
            )
            return tokenizer

        row = connection.execute(
            "SELECT value FROM retriever_meta WHERE key='tokenizer'"
        ).fetchone()
        return row[0] if row and row[0] in {"trigram", "unicode61"} else "trigram"

    def _sync_sources(
        self,
        connection: sqlite3.Connection,
        *,
        tokenizer: str,
        force: bool = False,
    ) -> int:
        self.episodes_root.mkdir(parents=True, exist_ok=True)
        source_rows = {
            row[0]: row
            for row in connection.execute(
                f"SELECT path, episode_id, session_id, mtime_ns, size FROM {self._SOURCE_TABLE}"
            ).fetchall()
        }
        current_paths: set[str] = set()
        indexed_count = 0
        for episode_path in sorted(self.episodes_root.glob("*.md")):
            resolved = episode_path.resolve()
            try:
                resolved.relative_to(self.episodes_root.resolve())
            except ValueError:
                continue
            relative_path = resolved.relative_to(self.root).as_posix()
            current_paths.add(relative_path)
            stat = resolved.stat()
            previous = source_rows.get(relative_path)
            if (
                not force
                and previous is not None
                and previous[3] == stat.st_mtime_ns
                and previous[4] == stat.st_size
            ):
                indexed_count += 1
                continue

            content = resolved.read_text(encoding="utf-8")
            episode_id, session_id = self._episode_identity(resolved, content)
            connection.execute(f"DELETE FROM {self._FTS_TABLE} WHERE path = ?", (relative_path,))
            logical_terms = self._logical_terms(self._content_for_search(content), max_terms=None)
            search_text = " ".join(self._fts_terms(logical_terms, tokenizer))
            connection.execute(
                f"""
                INSERT INTO {self._FTS_TABLE}
                    (episode_id, session_id, path, content, search_text)
                VALUES(?, ?, ?, ?, ?)
                """,
                (episode_id, session_id, relative_path, content, search_text),
            )
            connection.execute(
                f"""
                INSERT OR REPLACE INTO {self._SOURCE_TABLE}
                    (path, episode_id, session_id, mtime_ns, size)
                VALUES(?, ?, ?, ?, ?)
                """,
                (relative_path, episode_id, session_id, stat.st_mtime_ns, stat.st_size),
            )
            indexed_count += 1

        for stale_path in set(source_rows).difference(current_paths):
            connection.execute(f"DELETE FROM {self._FTS_TABLE} WHERE path = ?", (stale_path,))
            connection.execute(f"DELETE FROM {self._SOURCE_TABLE} WHERE path = ?", (stale_path,))
        connection.commit()
        return indexed_count

    @staticmethod
    def _episode_identity(path: Path, content: str) -> tuple[str, str]:
        session_match = re.search(r"^session_id:\s*(\S+)\s*$", content, flags=re.MULTILINE)
        episode_match = re.search(r"^episode_id:\s*(\S+)\s*$", content, flags=re.MULTILINE)
        episode_id = episode_match.group(1) if episode_match else path.stem
        if session_match:
            session_id = session_match.group(1)
        else:
            session_id = re.sub(r"-E\d+$", "", episode_id)
        return episode_id, session_id

    @staticmethod
    def _normalized_runs(text: str) -> list[str]:
        return [
            match.group(0).lower()
            for match in re.finditer(r"[0-9A-Za-z_\u3040-\u30ff\u3400-\u9fff]+", text)
            if match.group(0)
        ]

    @classmethod
    def _logical_terms(
        cls,
        text: str,
        *,
        max_terms: int | None = 32,
    ) -> list[str]:
        """Return explicit bi-grams used consistently by every FTS backend.

        One-character queries are intentionally unsupported. Two-character
        Japanese words such as 花火 must work even when native FTS5 trigram is
        available, so native tokenizer behavior is never the semantic boundary.
        """
        terms: list[str] = []
        for run in cls._normalized_runs(text):
            if len(run) < 2:
                continue
            for index in range(len(run) - 1):
                terms.append(run[index : index + 2])
        return cls._bounded_terms(list(dict.fromkeys(terms)), max_terms)

    @staticmethod
    def _fts_terms(logical_terms: list[str], tokenizer: str) -> list[str]:
        if tokenizer == "unicode61":
            return logical_terms
        # FTS5 trigram doesn't index 2-character tokens. Prefix each explicit
        # bi-gram with a stable sentinel so both indexed text and query contain
        # the same 3-character token.
        return [f"x{term}" for term in logical_terms]

    @staticmethod
    def _bounded_terms(terms: list[str], max_terms: int | None) -> list[str]:
        if max_terms is None or len(terms) <= max_terms:
            return terms
        if max_terms <= 1:
            return terms[:1]
        last_index = len(terms) - 1
        return [
            terms[round(index * last_index / (max_terms - 1))]
            for index in range(max_terms)
        ]

    @staticmethod
    def _content_for_search(content: str) -> str:
        return "\n".join(
            line
            for line in content.splitlines()
            if not line.strip().startswith(("session_id:", "episode_id:", "<!-- entry:"))
        )

    @staticmethod
    def _exclude_marked_entries(content: str, markers: set[str]) -> str:
        if not markers:
            return content
        lines = content.splitlines()
        kept: list[str] = []
        skip_entry_line = False
        for line in lines:
            stripped = line.strip()
            if stripped in markers:
                skip_entry_line = True
                continue
            if skip_entry_line and stripped.startswith("- "):
                skip_entry_line = False
                continue
            skip_entry_line = False
            kept.append(line)
        return "\n".join(kept)

    @classmethod
    def _episode_excerpt(
        cls,
        content: str,
        query_terms: set[str],
        limit: int = 1200,
    ) -> str:
        lines: list[str] = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("<!-- entry:"):
                continue
            if stripped.startswith("session_id:") or stripped.startswith("episode_id:"):
                continue
            lines.append(line)
        if not lines:
            return ""

        best_index = 0
        best_score = -1
        for index, line in enumerate(lines):
            line_terms = set(cls._logical_terms(line, max_terms=None))
            score = len(query_terms.intersection(line_terms))
            if score > best_score:
                best_index = index
                best_score = score

        start = max(0, best_index - 2)
        end = min(len(lines), best_index + 3)
        excerpt = "\n".join(lines[start:end]).strip()
        if len(excerpt) <= limit:
            return excerpt

        target = lines[best_index]
        if len(target) >= limit:
            match_offset = 0
            lowered = target.lower()
            for term in query_terms:
                found = lowered.find(term.lower())
                if found >= 0:
                    match_offset = found
                    break
            half = max(1, (limit - 2) // 2)
            crop_start = max(0, min(match_offset - half, len(target) - (limit - 1)))
            cropped = target[crop_start : crop_start + limit - 1]
            prefix = "…" if crop_start > 0 else ""
            suffix = "…" if crop_start + len(cropped) < len(target) else ""
            return (prefix + cropped + suffix)[:limit]

        return excerpt[: limit - 1].rstrip() + "…"
