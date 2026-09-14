from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import threading
import traceback
from typing import Any
from uuid import uuid4


_MAX_SUMMARY_CHARS = 800
_MAX_DETAIL_CHARS = 12_000
_MAX_RESOLVED_ROWS = 100
_CODE_TOKEN = re.compile(r"^[a-zA-Z0-9_.:-]{1,96}$")
_LOG_SCOPE_TOKEN = re.compile(r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>[^\s]+)")
_LOG_SCOPE_KEYS = frozenset({"provider", "operation", "role", "scope", "kind", "state", "error_type"})
_SQLITE_BUSY_TIMEOUT_SEC = 0.1
_FALLBACK_MAX_BYTES = 1_000_000
_FALLBACK_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _bounded(value: object, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _fingerprint(
    component: str,
    code: str,
    error_type: str | None = None,
    scope: str = "",
) -> str:
    parts = [
        component.strip().casefold(),
        code.strip().casefold(),
        (error_type or "").strip(),
    ]
    # Keep the original three-field fingerprint byte-for-byte when no scope is
    # supplied. Existing durable incidents such as memory_outbox_pending must
    # remain resolvable across this diagnostic refinement.
    if scope.strip():
        parts.append(scope.strip().casefold())
    material = "\x1f".join(parts)
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()


def _log_scope(message: str, exc: BaseException | None) -> str:
    """Keep stable fault dimensions without keying on volatile session ids.

    Provider/operation/scope differences are distinct repair incidents. IDs,
    PIDs and temporary paths are intentionally ignored so repeated occurrences
    of the same fault still collapse into one row.
    """
    values = [
        f"{match.group('key').casefold()}={match.group('value').strip(',;')}"
        for match in _LOG_SCOPE_TOKEN.finditer(message)
        if match.group("key").casefold() in _LOG_SCOPE_KEYS
    ]
    if exc is not None:
        os_code = getattr(exc, "winerror", None)
        if os_code is None:
            os_code = getattr(exc, "errno", None)
        if os_code is not None:
            values.append(f"os_error={os_code}")
    return "\x1e".join(sorted(set(values)))


class IncidentStoreError(RuntimeError):
    pass


class IncidentStore:
    """Small durable incident ledger for Holo-assisted repair.

    One SQLite file is used instead of per-incident directories. Repeated faults
    with the same component/code/error type reopen/update one row, so a noisy
    failure does not leave filesystem debris.
    """

    def __init__(self, root: Path) -> None:
        self.path = root.resolve() / "runtime" / "incidents.sqlite3"
        self.fallback_path = self.path.with_name("incidents-fallback.jsonl")
        self.fallback_replay_path = self.path.with_name("incidents-fallback.replay.jsonl")
        self.fallback_quarantine_path = self.path.with_name("incidents-fallback-quarantine.jsonl")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as connection:
                # WAL lets Holo health reads and ERROR incident writes coexist
                # without taking the rollback-journal writer lock against each
                # other. The setting persists with the database.
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS incidents (
                        incident_id TEXT PRIMARY KEY,
                        fingerprint TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK(status IN ('open', 'resolved')),
                        severity TEXT NOT NULL,
                        component TEXT NOT NULL,
                        code TEXT NOT NULL,
                        error_type TEXT,
                        summary TEXT NOT NULL,
                        detail TEXT NOT NULL,
                        first_seen TEXT NOT NULL,
                        last_seen TEXT NOT NULL,
                        occurrence_count INTEGER NOT NULL,
                        resolved_at TEXT,
                        resolution_note TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_incidents_status_last_seen
                    ON incidents(status, last_seen DESC);
                    CREATE TABLE IF NOT EXISTS incident_recoveries (
                        component TEXT NOT NULL,
                        code TEXT NOT NULL,
                        recovered_before TEXT NOT NULL,
                        note TEXT NOT NULL,
                        PRIMARY KEY(component, code)
                    );
                    """
                )
        except (OSError, sqlite3.Error) as exc:
            raise IncidentStoreError("Incident store could not be initialized") from exc

    def _connect(self) -> sqlite3.Connection:
        # Incident persistence is diagnostic. It gets a small lock window, then
        # ERROR logging falls back to one bounded durable JSONL instead of
        # stalling the Core event loop or silently losing the fault.
        connection = sqlite3.connect(self.path, timeout=_SQLITE_BUSY_TIMEOUT_SEC)
        connection.execute(f"PRAGMA busy_timeout={int(_SQLITE_BUSY_TIMEOUT_SEC * 1000)}")
        connection.row_factory = sqlite3.Row
        return connection

    def record(
        self,
        *,
        component: str,
        code: str,
        severity: str,
        summary: str,
        detail: str = "",
        error_type: str | None = None,
        fingerprint: str | None = None,
        observed_at: str | None = None,
    ) -> str:
        cleaned_component = _bounded(component, 160) or "core"
        cleaned_code = _bounded(code, 96) or "unknown_error"
        cleaned_severity = severity.strip().casefold()
        if cleaned_severity not in {"warning", "error", "critical"}:
            cleaned_severity = "error"
        cleaned_error_type = _bounded(error_type, 160) if error_type else None
        cleaned_summary = _bounded(summary, _MAX_SUMMARY_CHARS) or cleaned_code
        cleaned_detail = _bounded(detail, _MAX_DETAIL_CHARS)
        key = fingerprint or _fingerprint(cleaned_component, cleaned_code, cleaned_error_type)
        now = observed_at or _now_iso()
        try:
            if datetime.fromisoformat(now).tzinfo is None:
                now = _now_iso()
        except (TypeError, ValueError):
            now = _now_iso()
        incident_id = f"INC-{uuid4().hex}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO incidents (
                        incident_id, fingerprint, status, severity, component,
                        code, error_type, summary, detail, first_seen, last_seen,
                        occurrence_count, resolved_at, resolution_note
                    ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, 1, NULL, NULL)
                    ON CONFLICT(fingerprint) DO UPDATE SET
                        status='open',
                        severity=CASE
                            WHEN incidents.status='resolved' THEN excluded.severity
                            WHEN incidents.severity='critical' OR excluded.severity='critical' THEN 'critical'
                            WHEN incidents.severity='error' OR excluded.severity='error' THEN 'error'
                            ELSE 'warning'
                        END,
                        component=excluded.component,
                        code=excluded.code,
                        error_type=excluded.error_type,
                        summary=CASE WHEN julianday(excluded.last_seen) >= julianday(incidents.last_seen)
                            THEN excluded.summary ELSE incidents.summary END,
                        detail=CASE WHEN julianday(excluded.last_seen) >= julianday(incidents.last_seen)
                            THEN excluded.detail ELSE incidents.detail END,
                        last_seen=CASE WHEN julianday(excluded.last_seen) >= julianday(incidents.last_seen)
                            THEN excluded.last_seen ELSE incidents.last_seen END,
                        occurrence_count=incidents.occurrence_count + 1,
                        resolved_at=NULL,
                        resolution_note=NULL
                    """,
                    (
                        incident_id,
                        key,
                        cleaned_severity,
                        cleaned_component,
                        cleaned_code,
                        cleaned_error_type,
                        cleaned_summary,
                        cleaned_detail,
                        now,
                        now,
                    ),
                )
                self._apply_runtime_recoveries(connection)
                row = connection.execute(
                    "SELECT incident_id FROM incidents WHERE fingerprint=?",
                    (key,),
                ).fetchone()
                self._prune_resolved(connection)
                if row is None:
                    raise IncidentStoreError("Incident record disappeared after commit")
                return str(row["incident_id"])
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incident could not be recorded") from exc

    @staticmethod
    def _apply_runtime_recoveries(connection: sqlite3.Connection) -> None:
        connection.execute("""
            UPDATE incidents SET status='resolved', resolved_at=?, resolution_note=(
                SELECT note FROM incident_recoveries r
                WHERE r.component=incidents.component AND r.code=incidents.code
            ) WHERE status='open' AND EXISTS (
                SELECT 1 FROM incident_recoveries r
                WHERE r.component=incidents.component AND r.code=incidents.code
                AND julianday(incidents.last_seen) < julianday(r.recovered_before)
            )
        """, (_now_iso(),))

    def record_recovery(self, component: str, code: str, recovered_before: str, note: str) -> None:
        """Resolve only occurrences predating positive, scoped recovery evidence.

        Persist the cutoff so delayed fallback replay cannot reopen an old fault.
        A later occurrence retains the same fingerprint and reopens normally.
        """
        try:
            with self._connect() as connection:
                connection.execute("""
                    INSERT INTO incident_recoveries VALUES (?, ?, ?, ?)
                    ON CONFLICT(component, code) DO UPDATE SET
                        recovered_before=excluded.recovered_before, note=excluded.note
                    WHERE julianday(excluded.recovered_before) > julianday(incident_recoveries.recovered_before)
                """, (component, code, recovered_before, _bounded(note, 1200)))
                self._apply_runtime_recoveries(connection)
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incident recovery could not be recorded") from exc

    def resolve(self, incident_id: str, note: str = "") -> bool:
        cleaned = incident_id.strip()
        if not cleaned:
            return False
        now = _now_iso()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE incidents
                    SET status='resolved', resolved_at=?, resolution_note=?
                    WHERE incident_id=? AND status='open'
                    """,
                    (now, _bounded(note, 1200), cleaned),
                )
                self._prune_resolved(connection)
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incident could not be resolved") from exc

    def resolve_fingerprint(self, fingerprint: str, note: str = "") -> bool:
        now = _now_iso()
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """
                    UPDATE incidents
                    SET status='resolved', resolved_at=?, resolution_note=?
                    WHERE fingerprint=? AND status='open'
                    """,
                    (now, _bounded(note, 1200), fingerprint),
                )
                self._prune_resolved(connection)
                return cursor.rowcount > 0
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incident could not be resolved") from exc

    def unresolved(self, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded_limit = min(max(int(limit), 1), 100)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT incident_id, fingerprint, status, severity, component,
                           code, error_type, summary, detail, first_seen, last_seen,
                           occurrence_count, resolved_at, resolution_note
                    FROM incidents
                    WHERE status='open'
                    ORDER BY last_seen DESC
                    LIMIT ?
                    """,
                    (bounded_limit,),
                ).fetchall()
                return [dict(row) for row in rows]
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incidents could not be read") from exc

    def unresolved_count(self) -> int:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS count FROM incidents WHERE status='open'"
                ).fetchone()
                return int(row["count"] if row is not None else 0)
        except sqlite3.Error as exc:
            raise IncidentStoreError("Incident count could not be read") from exc

    def append_fallback(
        self,
        *,
        component: str,
        code: str,
        severity: str,
        summary: str,
        detail: str = "",
        error_type: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        """Durably preserve an ERROR when SQLite is temporarily unavailable.

        This is deliberately one bounded file, not one file per incident. It is
        replayed into SQLite on a later Dive/health check.
        """
        payload = {
            "component": _bounded(component, 160) or "core",
            "code": _bounded(code, 96) or "unknown_error",
            "severity": severity if severity in {"warning", "error", "critical"} else "error",
            "summary": _bounded(summary, _MAX_SUMMARY_CHARS) or "incident fallback",
            "detail": _bounded(detail, _MAX_DETAIL_CHARS),
            "error_type": _bounded(error_type, 160) if error_type else None,
            "fingerprint": fingerprint,
            "captured_at": _now_iso(),
        }
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with _FALLBACK_LOCK:
                overflow = False
                try:
                    overflow = self.fallback_path.is_file() and self.fallback_path.stat().st_size >= _FALLBACK_MAX_BYTES
                except OSError:
                    overflow = False
                if not overflow:
                    self._repair_fallback_tail_before_append()
                mode = "w" if overflow else "a"
                with self.fallback_path.open(mode, encoding="utf-8", newline="\n") as handle:
                    if overflow:
                        marker = {
                            "component": "nirai.core.incidents",
                            "code": "incident_fallback_overflow",
                            "severity": "error",
                            "summary": "Incident fallback journal reached its bounded size and was compacted",
                            "detail": "",
                            "error_type": None,
                            "fingerprint": None,
                            "captured_at": _now_iso(),
                        }
                        handle.write(json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n")
                    handle.write(line)
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise IncidentStoreError("Incident fallback journal could not be written") from exc

    def _repair_fallback_tail_before_append(self) -> None:
        if not self.fallback_path.is_file():
            return
        data = self.fallback_path.read_bytes()
        if not data or data.endswith(b"\n"):
            return
        last_newline = data.rfind(b"\n")
        tail_start = last_newline + 1
        tail = data[tail_start:]
        try:
            parsed = json.loads(tail.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            with self.fallback_path.open("ab") as handle:
                handle.write(b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            return

        self._quarantine_fallback_fragment(tail, reason="truncated_tail_before_append")
        with self.fallback_path.open("r+b") as handle:
            handle.truncate(tail_start)
            handle.flush()
            os.fsync(handle.fileno())
        marker = {
            "component": "nirai.core.incidents",
            "code": "incident_fallback_corrupt_record",
            "severity": "error",
            "summary": "Incident fallback contained a truncated tail; raw bytes were quarantined",
            "detail": "",
            "error_type": None,
            "fingerprint": None,
            "captured_at": _now_iso(),
        }
        with self.fallback_path.open("ab") as handle:
            handle.write((json.dumps(marker, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())

    def _quarantine_fallback_fragment(self, fragment: bytes, *, reason: str) -> None:
        encoded = fragment[:64_000].hex()
        record = {
            "captured_at": _now_iso(),
            "reason": reason,
            "fragment_hex": encoded,
            "fragment_bytes": len(fragment),
            "fragment_truncated": len(fragment) > 64_000,
        }
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            overflow = (
                self.fallback_quarantine_path.is_file()
                and self.fallback_quarantine_path.stat().st_size >= _FALLBACK_MAX_BYTES
            )
            mode = "w" if overflow else "a"
            with self.fallback_quarantine_path.open(mode, encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise IncidentStoreError("Incident fallback quarantine could not be written") from exc

    def fallback_pending(self) -> bool:
        try:
            with _FALLBACK_LOCK:
                return any(
                    path.is_file() and path.stat().st_size > 0
                    for path in (self.fallback_replay_path, self.fallback_path)
                )
        except OSError:
            return True

    def replay_fallback(self, *, limit: int = 32) -> int:
        """Replay a bounded slice of fallback incidents into SQLite.

        Atomic rename detaches the journal being replayed so a simultaneous
        ERROR can create a fresh fallback file without being lost. Interactive
        Dive health checks process at most a small slice; any remainder stays in
        the replay file for the next checkpoint.
        """
        try:
            with _FALLBACK_LOCK:
                if self.fallback_replay_path.is_file():
                    source = self.fallback_replay_path
                elif self.fallback_path.is_file() and self.fallback_path.stat().st_size > 0:
                    os.replace(self.fallback_path, self.fallback_replay_path)
                    source = self.fallback_replay_path
                else:
                    return 0
        except OSError as exc:
            raise IncidentStoreError("Incident fallback journal could not be staged for replay") from exc

        try:
            raw = source.read_bytes()
        except OSError as exc:
            raise IncidentStoreError("Incident fallback journal could not be read") from exc

        bounded_limit = min(max(int(limit), 1), 100)
        chunks = [chunk for chunk in raw.split(b"\n") if chunk.strip()]
        selected = chunks[:bounded_limit]
        remaining = chunks[bounded_limit:]
        replayed = 0
        try:
            for chunk in selected:
                try:
                    line = chunk.decode("utf-8")
                except UnicodeDecodeError:
                    self._quarantine_fallback_fragment(
                        chunk,
                        reason="invalid_utf8_during_replay",
                    )
                    self.record(
                        component="nirai.core.incidents",
                        code="incident_fallback_corrupt_record",
                        severity="error",
                        summary="Incident fallback contained an invalid UTF-8 record; raw bytes were quarantined",
                    )
                    replayed += 1
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    self._quarantine_fallback_fragment(
                        chunk,
                        reason="invalid_json_during_replay",
                    )
                    self.record(
                        component="nirai.core.incidents",
                        code="incident_fallback_corrupt_record",
                        severity="error",
                        summary="Incident fallback contained a corrupt record; raw bytes were quarantined",
                    )
                    replayed += 1
                    continue
                if not isinstance(item, dict):
                    self._quarantine_fallback_fragment(
                        chunk,
                        reason="non_object_during_replay",
                    )
                    self.record(
                        component="nirai.core.incidents",
                        code="incident_fallback_corrupt_record",
                        severity="error",
                        summary="Incident fallback contained a non-object record; raw bytes were quarantined",
                    )
                    replayed += 1
                    continue
                self.record(
                    component=str(item.get("component") or "nirai.core.incidents"),
                    code=str(item.get("code") or "fallback_error"),
                    severity=str(item.get("severity") or "error"),
                    summary=str(item.get("summary") or "Incident fallback entry"),
                    detail=str(item.get("detail") or ""),
                    error_type=(
                        str(item["error_type"])
                        if item.get("error_type") is not None
                        else None
                    ),
                    fingerprint=(
                        str(item["fingerprint"])
                        if item.get("fingerprint")
                        else None
                    ),
                    observed_at=item.get("captured_at"),
                )
                replayed += 1
        except IncidentStoreError:
            # Keep the detached replay journal intact for the next health check.
            raise

        try:
            with _FALLBACK_LOCK:
                if remaining:
                    temp = self.fallback_replay_path.with_suffix(".rewrite.tmp")
                    with temp.open("wb") as handle:
                        handle.write(b"\n".join(remaining) + b"\n")
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(temp, source)
                else:
                    source.unlink(missing_ok=True)
        except OSError as exc:
            raise IncidentStoreError("Incident fallback journal could not be checkpointed after replay") from exc
        return replayed

    def _prune_resolved(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM incidents
            WHERE status='resolved'
              AND incident_id NOT IN (
                  SELECT incident_id
                  FROM incidents
                  WHERE status='resolved'
                  ORDER BY resolved_at DESC, last_seen DESC
                  LIMIT ?
              )
            """,
            (_MAX_RESOLVED_ROWS,),
        )


class IncidentLogHandler(logging.Handler):
    """Turn ERROR+ Core log records into deduplicated durable incidents."""

    def __init__(self, store: IncidentStore) -> None:
        super().__init__(level=logging.ERROR)
        self.store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            first = message.split(maxsplit=1)[0] if message else "log_error"
            code = first if _CODE_TOKEN.fullmatch(first) else "log_error"
            error_type: str | None = None
            detail = ""
            exception: BaseException | None = None
            if record.exc_info is not None:
                error_type = record.exc_info[0].__name__ if record.exc_info[0] is not None else None
                exception = record.exc_info[1]
                detail = "".join(traceback.format_exception(*record.exc_info))
            severity = "critical" if record.levelno >= logging.CRITICAL else "error"
            fingerprint = _fingerprint(
                record.name,
                code,
                error_type,
                _log_scope(message, exception),
            )
        except Exception:
            # A malformed LogRecord must not recurse through logging.
            return

        try:
            self.store.record(
                component=record.name,
                code=code,
                severity=severity,
                summary=message,
                detail=detail,
                error_type=error_type,
                fingerprint=fingerprint,
            )
        except Exception:
            try:
                self.store.append_fallback(
                    component=record.name,
                    code=code,
                    severity=severity,
                    summary=message,
                    detail=detail,
                    error_type=error_type,
                    fingerprint=fingerprint,
                )
            except Exception:
                # Diagnostics must never become a new product failure or recurse
                # through logging while handling the original error.
                return


def memory_outbox_fingerprint() -> str:
    return _fingerprint("nirai.core.memory", "memory_outbox_pending", None)


def memory_outbox_unreadable_fingerprint() -> str:
    return _fingerprint("nirai.core.memory", "memory_outbox_unreadable", None)


def record_runtime_recovery(root: Path, component: str, code: str, recovered_before: str, note: str) -> None:
    try:
        IncidentStore(root).record_recovery(component, code, recovered_before, note)
    except IncidentStoreError:
        logging.getLogger("nirai.core.incidents").warning("incident_runtime_recovery_save_failed code=%s", code)
