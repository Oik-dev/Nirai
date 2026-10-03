"""イデアを守る3原則。設計書 §4.3。

1. 透明性: 無言で消さない。人格の改訂・記憶のページを外すことは、必ず日本語の変更レポートを残す（ChangeLog）
2. 可逆性: 人格のブロックを書き換える前に、前の文を控える（GenerationStore）。イデアそのものは毎晩 G: へ写す
3. 同一性: 人格の固定ブロックには触れない。可変ブロックの自律改訂は1回20%まで。本人が書いた記憶のページは書き換えない
   （core/memory/page.py の Page.with_words）
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from mind.core.idea import DATA_DIR

DEFAULT_CHANGE_LOG_PATH = DATA_DIR / "change_log.jsonl"
DEFAULT_GENERATION_STORE_PATH = DATA_DIR / "generations.jsonl"

MAX_AUTONOMOUS_CHANGE_RATIO = 0.2


class ProtectionError(Exception):
    """保護原則違反を示す例外。"""


@dataclass(frozen=True)
class ChangeReport:
    """変更レポート（原則1: いつ・何を・なぜ・どう変えたか）。target_id は対象の目印（ページの id など）。"""

    timestamp: str
    action: str
    target_id: int | str
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
    """人格ブロックを書き換える前の控え（原則2: 可逆性）。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def save_persona_block(self, block_id: str, content: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "persona_block_id": block_id,
            "content": content,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

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
