"""保護3原則。設計書v2 §4.3

1. 透明性: 無言破棄の禁止。統合・削除・書き換えは必ず日本語の変更レポートを残す
2. 可逆性: 消える前に必ず控えを取る（世代保存）
3. 同一性: 保護等級Sの変更はマスター承認のみ
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from serina.core_v2.memory.store import MemoryRecord

DEFAULT_CHANGE_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "change_log.jsonl"
DEFAULT_GENERATION_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "generations.jsonl"


class ProtectionError(Exception):
    """保護原則違反（主に同一性: 等級Sの無承認変更）を示す例外。"""


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

    def has_generation(self, record_id: int) -> bool:
        if not self._path.exists():
            return False
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line)["record_id"] == record_id:
                return True
        return False


def apply_protected_change(
    *,
    record: MemoryRecord,
    action: str,
    reason: str,
    new_content: str | None,
    change_log: ChangeLog,
    generation_store: GenerationStore,
    master_approved: bool = False,
) -> None:
    """変更を保護3原則に通す。実際のDB更新は呼び出し側（memory_review等）の責務。"""
    if record.protection_grade == "S" and not master_approved:
        raise ProtectionError(
            f"保護等級Sの記憶(id={record.id})はマスター承認なしに変更できない（§4.3 原則3）"
        )

    # 原則2: 消える前に必ず控えを取る
    generation_store.save_generation(record.id, record.content)

    # 原則1: 無言破棄の禁止。変更レポートを残す
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
