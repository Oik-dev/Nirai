"""残弾台帳。設計書 §2.6, §3.2④残弾チェック

Brainごとの日次残り回数・直近1分の使用数を管理する。日付が変われば日次分は自動リセットする。
"""

from __future__ import annotations

import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

UNLIMITED = -1
_MINUTE_WINDOW = timedelta(seconds=60)

DEFAULT_PERSIST_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "quota_ledger.json"


@dataclass(frozen=True)
class QuotaSpec:
    """裏方便(蒸留・日記)のクラウド発注が参照する残弾台帳の対象Brain（2026-07-12追加）。

    元々は会話用のクラウドBrain（旧"gemini_flash_lite"）と同一の残弾を共有する設計だった
    （§3.3「Gemini の余り弾」）。2026-07-18のBrain構成刷新（§9.3）で裏方便のcloud車線が
    永久退役したため、現状このクラス自体は生成されない（呼び出し元は消滅済み）。汎用の
    残弾管理機構として型だけ残す（将来cloud車線が復活する場合の再利用を想定）。
    """

    name: str
    daily_quota: int
    per_minute_quota: int


class QuotaLedger:
    """残弾台帳（§2.6状態目録・§3.2④）。

    `persist_path`を指定すると日次カウンタ（`_daily_used`/`_daily_date`）を
    使用のたびに即時保存し、再起動後もその日の消費量を引き継ぐ（再起動でリセットされると
    無料枠の日次上限を実際より多く使い切れると誤認するリスクがあるため。DECISIONS 2026-07-11
    持ち越しI-1）。直近1分の使用窓（`_minute_uses`）は保存しない
    （再起動は通常60秒を超えるため保存する価値が薄く、忘れても最大1分のバーストで自己修復する）。
    """

    def __init__(self, *, persist_path: str | Path | None = None) -> None:
        self._daily_used: dict[str, int] = defaultdict(int)
        self._daily_date: dict[str, object] = {}
        self._minute_uses: dict[str, deque[datetime]] = defaultdict(deque)
        self._persist_path = Path(persist_path) if persist_path else None
        if self._persist_path is not None:
            self._load()

    def _load(self) -> None:
        assert self._persist_path is not None
        if not self._persist_path.exists():
            return
        data = json.loads(self._persist_path.read_text(encoding="utf-8"))
        self._daily_used = defaultdict(int, data.get("daily_used", {}))
        self._daily_date = {
            name: date.fromisoformat(iso) for name, iso in data.get("daily_date", {}).items()
        }

    def _save(self) -> None:
        # レビュー(Important): 一時ファイル+os.replaceでアトミックに置換する
        # （routing_rules.pyの_saveと同じ理由。詳細はそちらのコメント参照）。
        if self._persist_path is None:
            return
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "daily_used": dict(self._daily_used),
            "daily_date": {name: d.isoformat() for name, d in self._daily_date.items()},
        }
        tmp_path = self._persist_path.with_suffix(self._persist_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, self._persist_path)

    def can_use(self, brain_name: str, *, daily_quota: int, per_minute_quota: int, now: datetime) -> bool:
        self._roll_day_if_needed(brain_name, now)
        if daily_quota != UNLIMITED and self._daily_used[brain_name] >= daily_quota:
            return False
        if per_minute_quota != UNLIMITED:
            self._evict_old_minute_uses(brain_name, now)
            if len(self._minute_uses[brain_name]) >= per_minute_quota:
                return False
        return True

    def record_use(self, brain_name: str, *, now: datetime) -> None:
        self._roll_day_if_needed(brain_name, now)
        self._daily_used[brain_name] += 1
        self._minute_uses[brain_name].append(now)
        self._save()

    def _roll_day_if_needed(self, brain_name: str, now: datetime) -> None:
        today = now.date()
        if self._daily_date.get(brain_name) != today:
            self._daily_date[brain_name] = today
            self._daily_used[brain_name] = 0

    def _evict_old_minute_uses(self, brain_name: str, now: datetime) -> None:
        window = self._minute_uses[brain_name]
        while window and (now - window[0]) >= _MINUTE_WINDOW:
            window.popleft()
