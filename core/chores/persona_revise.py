"""persona 可変ブロックの自律改訂。合意台帳 §2.1 / §3.9 / Wave 4 A9 本体。

manifest の mutable を参照し、固定ブロックは assert_persona_block_writable で遮断。
改訂前に backup_db（B7）と GenerationStore 控えを取る。
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from serina.core.memory.protection import (
    MAX_AUTONOMOUS_CHANGE_RATIO,
    ChangeLog,
    ChangeReport,
    GenerationStore,
    ProtectionError,
    assert_persona_block_writable,
)
from serina.core.persona_assets import DEFAULT_PERSONA_DIR, PersonaBlock, load_persona_assets

# ChangeLog 用の persona ブロック専用 id（記憶 id と衝突しない）
_PERSONA_TARGET_IDS: dict[str, int] = {
    "personality": 900_001,
    "voice": 900_002,
    "love": 900_003,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_block_change_ratio(before: str, after: str) -> float:
    """§2.1-1: ブロック単位の改訂幅（追加・削除・置換の合算 ÷ 変更前文字数）。"""
    if not before:
        return 1.0 if after else 0.0
    matcher = difflib.SequenceMatcher(a=before, b=after)
    delta = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "delete":
            delta += i2 - i1
        elif tag == "insert":
            delta += j2 - j1
        elif tag == "replace":
            delta += (i2 - i1) + (j2 - j1)
    return min(1.0, delta / len(before))


def _find_block(blocks: tuple[PersonaBlock, ...], block_id: str) -> PersonaBlock:
    for block in blocks:
        if block.id == block_id:
            return block
    raise ValueError(f"manifest に無いブロック id: {block_id}")


def revise_persona_block(
    block_id: str,
    new_content: str,
    *,
    reason: str,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    persona_dir: Path | str | None = None,
    mood_contaminated: bool = False,
    backup_db_fn: Callable[..., Path] | None = None,
    db_path: Path | str | None = None,
    backup_dir: Path | str | None = None,
) -> None:
    """可変 persona ブロックを §2.1 の4条件下で改訂する。"""
    directory = Path(persona_dir) if persona_dir is not None else DEFAULT_PERSONA_DIR
    assets = load_persona_assets(directory)
    block = _find_block(assets.blocks, block_id)

    assert_persona_block_writable(mutable=block.mutable)

    if mood_contaminated:
        raise ProtectionError("気分混入は persona 可変ブロック自律改訂に反映できない")

    change_ratio = compute_block_change_ratio(block.text, new_content)
    if change_ratio > MAX_AUTONOMOUS_CHANGE_RATIO:
        raise ProtectionError(
            f"改訂幅 {change_ratio:.0%} が上限 {MAX_AUTONOMOUS_CHANGE_RATIO:.0%} を超える"
        )

    # B7: 破壊的改訂の前に backup（呼び出し元が差し替え可能。未指定なら実関数）
    if backup_db_fn is not None:
        backup_db_fn(db_path, backup_dir)
    elif db_path is not None:
        from tools.backup_db import backup_db

        backup_db(db_path, backup_dir)

    generation_store.save_persona_block(block_id, block.text)

    block_path = directory / block.file
    block_path.write_text(new_content, encoding="utf-8")

    # 変更レポートは実ファイル反映の成功後に記録する（書き込み失敗時に
    # 「改訂した」という過大報告だけが残るのを防ぐ）
    target_id = _PERSONA_TARGET_IDS.get(block_id, 900_000)
    change_log.record(
        ChangeReport(
            timestamp=_utc_now_iso(),
            action="personaブロック改訂",
            target_id=target_id,
            reason=reason,
            before=block.text,
            after=new_content,
        )
    )
