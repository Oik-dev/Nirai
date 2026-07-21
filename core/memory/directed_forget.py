"""指示忘却（マスターの「それ忘れて」）。合意台帳 §3.5 / Wave 2。

候補提示→tombstone。物理削除は明示時のみ。正典9件（pinned=1）は禁止。
等級S（非正典）は master_confirmed_s 必須。破壊前に backup_db を呼ぶ。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from serina.core.memory.facts import FactStore
from serina.core.memory.protection import (
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
    apply_protected_change,
)
from serina.core.memory.store import MemoryRecord, MemoryStore

DEFAULT_BACKUP_DIR = Path(r"G:\SerinaDB Backup")


@dataclass(frozen=True)
class ForgetCandidate:
    """忘却候補（記憶または fact）。"""

    kind: str  # "memory" | "fact"
    target_id: str
    preview: str
    protection_grade: str | None = None
    pinned: bool = False


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def propose_forget_candidates(store: MemoryStore, query: str, *, top_k: int = 5) -> list[ForgetCandidate]:
    """Recall で忘却候補を提示する（tombstone 済みは store.recall が除外）。"""
    candidates: list[ForgetCandidate] = []
    for record in store.recall(query, top_k=top_k):
        pinned = store.is_pinned(record.id)
        candidates.append(
            ForgetCandidate(
                kind="memory",
                target_id=str(record.id),
                preview=record.content[:120],
                protection_grade=record.protection_grade,
                pinned=pinned,
            )
        )
    return candidates


def confirm_forget(
    store: MemoryStore,
    *,
    memory_id: int | None = None,
    fact_id: str | None = None,
    physical_delete: bool = False,
    master_confirmed_s: bool = False,
    reason: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    backup_dir: Path | str | None = None,
    fact_store: FactStore | None = None,
    skip_backup: bool = False,
) -> None:
    """指示忘却を確定する。memory または fact のどちらか一方を指定。

    skip_backup: 呼び出し元が同一の破壊操作内で既に控えを取っている場合に True。
    連続呼び出し（カスケード削除等）で毎回 backup_db を回すと、7世代ローテーションが
    操作開始前の復元点自体を押し出してしまうため、その場合は呼び出し元が操作全体で
    1回だけ backup_db を呼び、個々の呼び出しでは skip_backup=True を渡すこと（C-2）。
    """
    if (memory_id is None) == (fact_id is None):
        raise ValueError("memory_id または fact_id のどちらか一方を指定すること")

    if memory_id is not None:
        _confirm_forget_memory(
            store,
            memory_id=memory_id,
            physical_delete=physical_delete,
            master_confirmed_s=master_confirmed_s,
            reason=reason,
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=backup_dir,
            skip_backup=skip_backup,
        )
    else:
        assert fact_id is not None
        _confirm_forget_fact(
            store,
            fact_id=fact_id,
            physical_delete=physical_delete,
            master_confirmed_s=master_confirmed_s,
            reason=reason,
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=backup_dir,
            fact_store=fact_store or FactStore(store._db_path),  # noqa: SLF001
            skip_backup=skip_backup,
        )


def _confirm_forget_memory(
    store: MemoryStore,
    *,
    memory_id: int,
    physical_delete: bool,
    master_confirmed_s: bool,
    reason: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    backup_dir: Path | str | None,
    skip_backup: bool = False,
) -> None:
    record = store.get_memory_by_id(memory_id)
    if record is None:
        raise ProtectionError(f"記憶 id={memory_id} が見つからない")

    memory_record, pinned = record
    if pinned:
        raise ProtectionError(f"正典固定記憶(id={memory_id}, pinned=1)は指示忘却禁止")

    if memory_record.protection_grade == "S" and not master_confirmed_s:
        raise ProtectionError(f"保護等級Sの記憶(id={memory_id})は master_confirmed_s が必要")

    # 可逆性: 破壊的変更の前に backup（B7）→ 変更レポート／控え → tombstone/削除
    if not skip_backup:
        _backup_before_destructive(store, backup_dir)
    apply_protected_change(
        record=memory_record,
        action="指示忘却" if not physical_delete else "指示忘却（物理削除）",
        reason=reason,
        new_content=None,
        change_log=change_log,
        generation_store=generation_store,
        master_approved=master_confirmed_s,
        pinned=pinned,
    )
    if physical_delete:
        store.physical_delete_memory(memory_id)
    else:
        store.tombstone_memory(memory_id, reason=reason)


def _confirm_forget_fact(
    store: MemoryStore,
    *,
    fact_id: str,
    physical_delete: bool,
    master_confirmed_s: bool,
    reason: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    backup_dir: Path | str | None,
    fact_store: FactStore,
    skip_backup: bool = False,
) -> None:
    fact = fact_store.get_fact(fact_id)
    if fact is None:
        raise ProtectionError(f"fact id={fact_id} が見つからない")

    if not skip_backup:
        _backup_before_destructive(store, backup_dir)

    generation_store.save_generation(0, fact.statement)
    change_log.record(
        ChangeReport(
            timestamp=_utc_now_iso(),
            action="指示忘却（fact tombstone）" if not physical_delete else "指示忘却（fact 物理削除）",
            target_id=0,
            reason=reason,
            before=fact.statement,
            after=None,
        )
    )

    if physical_delete:
        conn = fact_store._connect()  # noqa: SLF001
        try:
            conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
            conn.commit()
        finally:
            conn.close()
    else:
        fact_store.tombstone_fact(fact_id)


def _backup_before_destructive(store: MemoryStore, backup_dir: Path | str | None) -> None:
    from tools.backup_db import backup_db

    backup_db(Path(store._db_path), backup_dir or DEFAULT_BACKUP_DIR, keep_generations=7)  # noqa: SLF001
