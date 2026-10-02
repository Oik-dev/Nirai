"""保護3原則。設計書 §4.3（2026-07-19改訂: 同一性は事後監査制）

1. 透明性: 無言破棄の禁止。統合・削除・書き換えは必ず日本語の変更レポートを残す
2. 可逆性: 消える前に必ず控えを取る（世代保存）
3. 同一性: 保護等級Sは事後監査制（4条件）。正典固定・persona固定ブロックは不触
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from serina.core.memory.store import MemoryRecord

DEFAULT_CHANGE_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "change_log.jsonl"
DEFAULT_GENERATION_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "generations.jsonl"

MAX_AUTONOMOUS_CHANGE_RATIO = 0.2


class ProtectionError(Exception):
    """保護原則違反を示す例外。"""


@dataclass(frozen=True)
class ChangeReport:
    """変更レポート（原則1: いつ・何を・なぜ・どう変えたか）"""

    timestamp: str
    action: str
    target_id: int
    reason: str
    before: str | None
    after: str | None


class ChangeLog:
    """変更レポートの追記専用ログ（原則1: 無言破棄の禁止）"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def record(self, report: ChangeReport) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(report), ensure_ascii=False) + "\n")

    def read_all(self) -> list[ChangeReport]:
        if not self._path.exists():
            return []
        reports = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                reports.append(ChangeReport(**json.loads(line)))
        return reports


class GenerationStore:
    """破壊的変更前の控え（原則2: 可逆性。戻せるなら触っていい）"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def save_generation(self, record_id: int, content: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "record_id": record_id,
            "content": content,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_persona_block(self, block_id: str, content: str) -> None:
        """persona ブロック改訂前の控え（原則2）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "persona_block_id": block_id,
            "content": content,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def has_generation(self, record_id: int) -> bool:
        if not self._path.exists():
            return False
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("record_id") == record_id:
                return True
        return False

    def has_persona_block(self, block_id: str) -> bool:
        if not self._path.exists():
            return False
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("persona_block_id") == block_id:
                return True
        return False


def assert_persona_block_writable(*, mutable: bool) -> None:
    """persona 固定ブロックへの書き込みを構造的に遮断する。"""
    if not mutable:
        raise ProtectionError("persona 固定ブロック(mutable=false)への書き込みは禁止")


def apply_protected_change(
    *,
    record: MemoryRecord,
    action: str,
    reason: str,
    new_content: str | None,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    master_approved: bool = False,
    is_canonical: bool = False,
    pinned: bool = False,
    change_ratio: float | None = None,
    mood_contaminated: bool = False,
) -> None:
    """変更を保護3原則に通す。実際のDB更新は呼び出し側の責務。

    正典固定（is_canonical または pinned）→ 常に禁止（master_approved でも不可）。
    非正典S: 旧 master_approved 必須を廃止し、4条件（改訂幅・気分混入・レポート・控え）で事後監査。
    master_approved=True は手動最終権限として引き続き全許可（正典除く）。
    """
    if is_canonical or pinned:
        raise ProtectionError(
            f"正典固定記憶(id={record.id})は変更・忘却禁止（§4.3 原則3-4）"
        )

    if master_approved:
        pass
    elif record.protection_grade == "S":
        if mood_contaminated:
            raise ProtectionError("気分混入は persona / 等級S 自律改訂に反映できない")
        if change_ratio is not None and change_ratio > MAX_AUTONOMOUS_CHANGE_RATIO:
            raise ProtectionError(
                f"改訂幅 {change_ratio:.0%} が上限 {MAX_AUTONOMOUS_CHANGE_RATIO:.0%} を超える"
            )

    generation_store.save_generation(record.id, record.content)

    change_log.record(
        ChangeReport(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action=action,
            target_id=record.id,
            reason=reason,
            before=record.content,
            after=new_content,
        )
    )
