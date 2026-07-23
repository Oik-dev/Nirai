"""GUIメンテからの手動編集（本文content・保護等級S/A/B）を保護3原則に通す。

`directed_forget.py`（削除）と同じ層・同じ責務パターン。新しい保護規範は作らず、
既存の `protection.apply_protected_change`（pinned禁止・S級ガード・変更ログ・世代控え）を
そのまま再利用する。実DB書き込みは呼び出し側の責務（`MemoryStore.update_memory_fields`）。
"""

from __future__ import annotations

from pathlib import Path

from serina.core.memory.protection import (
    ChangeLog,
    GenerationStore,
    ProtectionError,
    apply_protected_change,
)
from serina.core.memory.store import MemoryStore

VALID_GRADES = ("S", "A", "B")

MASTER_EDIT_REASON = "マスター手動（GUIメンテ編集）"

DEFAULT_BACKUP_DIR = Path(r"G:\SerinaDB Backup")


def edit_memory(
    store: MemoryStore,
    *,
    memory_id: int,
    new_content: str | None = None,
    new_protection_grade: str | None = None,
    reason: str = MASTER_EDIT_REASON,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    backup_dir: Path | str | None = None,
    skip_backup: bool = False,
) -> None:
    """記憶の本文・保護等級をマスターがGUIから編集する。

    content・protection_gradeの少なくとも一方を指定する。pinned（正典固定）は常に禁止。
    等級S絡み（変更前または変更後がS）はGUI確認ダイアログ通過をもって許可する
    （削除の`master_confirmed_s`と同じ考え方。同一性原則§4.3のC-2は
    apply_protected_changeのis_canonical/pinnedガードで維持される）。
    """
    if new_content is None and new_protection_grade is None:
        raise ValueError("new_content または new_protection_grade の少なくとも一方を指定すること")
    if new_content is not None and not new_content.strip():
        raise ValueError("new_content を空にはできない")
    if new_protection_grade is not None and new_protection_grade not in VALID_GRADES:
        raise ValueError(f"protection_grade は {VALID_GRADES} のいずれか")

    pair = store.get_memory_by_id(memory_id)
    if pair is None:
        raise ProtectionError(f"記憶 id={memory_id} が見つからない")
    record, pinned = pair

    grade_before = record.protection_grade
    grade_after = new_protection_grade if new_protection_grade is not None else grade_before
    grade_touches_s = grade_before == "S" or grade_after == "S"

    if not skip_backup:
        _backup_before_destructive(store, backup_dir)

    content_after = new_content if new_content is not None else record.content
    edit_reason = reason
    if new_protection_grade is not None and new_protection_grade != grade_before:
        edit_reason = f"{reason}（保護等級 {grade_before}→{grade_after}）"

    apply_protected_change(
        record=record,
        action="内容編集" if new_content is not None else "保護等級変更",
        reason=edit_reason,
        new_content=content_after,
        change_log=change_log,
        generation_store=generation_store,
        master_approved=grade_touches_s,
        pinned=pinned,
    )

    store.update_memory_fields(
        memory_id,
        content=new_content,
        protection_grade=new_protection_grade,
    )


def _backup_before_destructive(store: MemoryStore, backup_dir: Path | str | None) -> None:
    from tools.backup_db import backup_db

    backup_db(Path(store._db_path), backup_dir or DEFAULT_BACKUP_DIR, keep_generations=7)  # noqa: SLF001
