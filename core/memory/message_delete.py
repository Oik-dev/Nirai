"""発言単位削除の副作用処理（§4.8.1 GUIメンテ削除）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone

from serina.core.chores.chore_box import ChoreBox
from serina.core.memory.diary_cascade import collect_diary_material_targets
from serina.core.memory.directed_forget import confirm_forget
from serina.core.memory.protection import ChangeLog, ChangeReport, GenerationStore
from serina.core.memory.session_store import SessionStore
from serina.core.memory.store import MemoryStore
from serina.core.state.session import SessionState, Turn

MASTER_DELETE_REASON = "マスター手動（GUI発言削除・物理削除）"


@dataclass
class SessionPurgeOutcome:
    """セッション削除に伴う副作用（発言1件削除の①②③をセッション全体に適用した結果）。"""

    chore_jobs_removed: list[int] = field(default_factory=list)
    memories_deleted: list[int] = field(default_factory=list)
    memories_quote_trimmed: list[int] = field(default_factory=list)
    diary_trace_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class MessageDeleteOutcome:
    ok: bool
    message_id: int
    session_id: str
    role: str
    content_preview: str
    chore_jobs_removed: list[int] = field(default_factory=list)
    memories_deleted: list[int] = field(default_factory=list)
    memories_quote_trimmed: list[int] = field(default_factory=list)
    diary_trace_notes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    core_session_synced: bool = False


def _role_to_speaker(role: str) -> str:
    return "master" if role == "user" else "serina"


def rebuild_session_turns_from_history(
    session: SessionState,
    history_rows: list[dict],
) -> None:
    """現行セッションの Core ターンを帳簿履歴から作り直す（ID取り違え防止）。"""
    turns: list[Turn] = []
    for row in history_rows:
        role = str(row.get("role") or "")
        content = str(row.get("content") or "")
        if role not in ("user", "assistant") or not content:
            continue
        turns.append(Turn(speaker=_role_to_speaker(role), text=content))
    session.turns = turns


def _parse_metadata(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _source_quotes(metadata: dict) -> list[str]:
    """出所引用。未保存の旧記憶は空（内容一致での物理削除はしない）。"""
    raw = metadata.get("source_quotes")
    if isinstance(raw, list):
        quotes = [q for q in raw if isinstance(q, str) and q.strip()]
        if quotes:
            return quotes
    legacy = metadata.get("source_quote")
    if isinstance(legacy, str) and legacy.strip():
        return [legacy.strip()]
    return []


def _quote_matches_deleted(quote: str, deleted_text: str) -> bool:
    """物理削除・引用除去は完全一致のみ（包含一致は誤爆するため禁止）。"""
    if not quote or not deleted_text:
        return False
    return quote == deleted_text


def _diary_material_trace_notes(
    store: MemoryStore,
    *,
    message_ts: str,
) -> list[str]:
    """削除発言が日記材料窓に入っていた可能性を列挙（本文は改変しない）。"""
    notes: list[str] = []
    diaries = store.list_by_type("diary", limit=200)
    for diary in diaries:
        targets = collect_diary_material_targets(store, diary)
        window_start = None
        if targets:
            window_start = min(t.created_at for t in targets)
        elif diary.created_at:
            window_start = diary.created_at
        if window_start is None:
            continue
        if window_start <= message_ts <= diary.created_at:
            notes.append(
                f"日記 id={diary.id}（{diary.created_at[:10]}）の材料窓に該当する可能性"
            )
    return notes


def purge_chores_for_utterance(
    chore_box: ChoreBox,
    *,
    text: str,
    role: str,
) -> list[int]:
    """当該発言を含む未消化宿題を除去。"""
    speaker = _role_to_speaker(role)
    removed: list[int] = []
    for job in chore_box.pending():
        turns = job.payload.get("turns")
        if not isinstance(turns, list):
            continue
        matched = any(
            isinstance(t, dict)
            and t.get("speaker") == speaker
            and t.get("text") == text
            for t in turns
        )
        if matched:
            chore_box.mark_done(job.id)
            removed.append(job.id)
    return removed


def reconcile_distilled_memories(
    store: MemoryStore,
    *,
    deleted_text: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    skip_backup: bool = True,
) -> tuple[list[int], list[int], list[str]]:
    """蒸留済み記憶の出所引用を整理。戻り値: (物理削除id, 引用除去id, 注記)"""
    deleted: list[int] = []
    trimmed: list[int] = []
    notes: list[str] = []

    conn = store._connect()  # noqa: SLF001
    try:
        rows = conn.execute(
            """
            SELECT m.* FROM memories m
            LEFT JOIN memory_tombstones t ON t.memory_id = m.id
            WHERE t.memory_id IS NULL
              AND m.protection_grade = 'B'
              AND (m.source IS NULL OR TRIM(m.source) = '')
              AND m.pinned = 0
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        metadata = _parse_metadata(row["metadata"] if "metadata" in row.keys() else None)
        quotes = _source_quotes(metadata)
        if not quotes:
            # 旧記憶: 内容一致は注記のみ（物理削除しない）
            if deleted_text and deleted_text in (row["content"] or ""):
                notes.append(
                    f"memory id={row['id']}: source_quotes 未保存のため内容一致のみ検出（要確認）"
                )
            continue

        matching = [q for q in quotes if _quote_matches_deleted(q, deleted_text)]
        if not matching:
            continue

        remaining = [q for q in quotes if q not in matching]
        memory_id = int(row["id"])
        if not remaining:
            confirm_forget(
                store,
                memory_id=memory_id,
                physical_delete=True,
                master_confirmed_s=False,
                reason=MASTER_DELETE_REASON,
                change_log=change_log,
                generation_store=generation_store,
                skip_backup=skip_backup,
            )
            deleted.append(memory_id)
        else:
            metadata["source_quotes"] = remaining
            metadata.pop("source_quote", None)
            conn = store._connect()  # noqa: SLF001
            try:
                conn.execute(
                    "UPDATE memories SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata, ensure_ascii=False), memory_id),
                )
                conn.commit()
            finally:
                conn.close()
            trimmed.append(memory_id)

    return deleted, trimmed, notes


def purge_effects_for_session_rows(
    rows: list[dict],
    *,
    chore_box: ChoreBox | None,
    memory_store: MemoryStore,
    change_log: ChangeLog,
    generation_store: GenerationStore,
) -> SessionPurgeOutcome:
    """セッション削除向け: 発言1件削除の①②③（宿題除去・記憶引用整理・日記材料痕跡）を
    セッション内の全発言に対してまとめて実行する（テスト会話→セッション削除で
    「会話していなかった」に近い状態へ戻すためのマスター要望、2026-07-23）。

    記憶の物理削除を伴うため、呼び出し側で `backup_db` 済みであることが前提
    （`reconcile_distilled_memories` は `skip_backup=True` で複数回呼ぶ）。
    """
    outcome = SessionPurgeOutcome()
    diary_notes: list[str] = []
    for row in rows:
        role = str(row.get("role") or "")
        content = str(row.get("content") or "")
        if not content:
            continue
        if chore_box is not None:
            outcome.chore_jobs_removed.extend(
                purge_chores_for_utterance(chore_box, text=content, role=role)
            )
        mem_deleted, mem_trimmed, mem_notes = reconcile_distilled_memories(
            memory_store,
            deleted_text=content,
            change_log=change_log,
            generation_store=generation_store,
            skip_backup=True,
        )
        outcome.memories_deleted.extend(mem_deleted)
        outcome.memories_quote_trimmed.extend(mem_trimmed)
        # mem_notes は「要確認」等の記憶側注記であり、日記材料痕跡とは別種（単発削除
        # 経路 delete_message_with_effects と同じ notes 扱いに揃える。誤って
        # diary_trace_notes に混ぜると GUI/change_log 上で分類が誤る）
        outcome.notes.extend(mem_notes)
        ts = str(row.get("ts") or "")
        if ts:
            diary_notes.extend(_diary_material_trace_notes(memory_store, message_ts=ts))
    # 同一日記材料窓に複数発言が該当すると同じ注記が発言数ぶん重複するため、
    # 順序を保ったまま去重する
    seen: set[str] = set()
    for note in diary_notes:
        if note not in seen:
            seen.add(note)
            outcome.diary_trace_notes.append(note)
    return outcome


def delete_message_with_effects(
    *,
    session_store: SessionStore,
    memory_store: MemoryStore,
    chore_box: ChoreBox | None,
    session: SessionState | None,
    message_id: int,
    current_session_id: str | None,
    change_log: ChangeLog,
    generation_store: GenerationStore,
) -> MessageDeleteOutcome:
    """会話帳簿から1発言を削除し、関連副作用を処理する。"""
    row = session_store.get_message(message_id)
    if row is None:
        raise LookupError(f"message id={message_id} が見つからない")

    deleted = session_store.delete_message(message_id)
    if deleted is None:
        raise LookupError(f"message id={message_id} の削除に失敗")

    role = str(deleted["role"])
    content = str(deleted["content"])
    session_id = str(deleted["session_id"])
    preview = content[:120]

    outcome = MessageDeleteOutcome(
        ok=True,
        message_id=message_id,
        session_id=session_id,
        role=role,
        content_preview=preview,
    )

    # Core SessionState は現行セッションのときだけ、帳簿から再構築する（文字列一致で消さない）
    if session is not None and current_session_id and session_id == current_session_id:
        history = session_store.get_session_history(session_id)
        rebuild_session_turns_from_history(session, history)
        outcome.core_session_synced = True
    elif session is not None:
        outcome.notes.append(
            f"過去セッション {session_id} の削除のため Core SessionState は非接触"
        )

    if chore_box is not None:
        outcome.chore_jobs_removed = purge_chores_for_utterance(
            chore_box, text=content, role=role,
        )
    else:
        outcome.notes.append("chore_box 未設定のため宿題除去スキップ")

    mem_deleted, mem_trimmed, mem_notes = reconcile_distilled_memories(
        memory_store,
        deleted_text=content,
        change_log=change_log,
        generation_store=generation_store,
        skip_backup=True,
    )
    outcome.memories_deleted = mem_deleted
    outcome.memories_quote_trimmed = mem_trimmed
    outcome.notes.extend(mem_notes)

    ts = str(deleted.get("ts") or datetime.now(timezone.utc).isoformat())
    outcome.diary_trace_notes = _diary_material_trace_notes(memory_store, message_ts=ts)

    return outcome
