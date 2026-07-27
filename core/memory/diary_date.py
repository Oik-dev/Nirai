"""日記の対象日ラベル解決。表示・削除警告・export・persona提案の共通口。

日記(episodic)の`created_at`は材料窓のため「対象Serina日の終わり」（次Serina日の
開始瞬間）で保存される。表示用の「何日の日記か」は`metadata.target_date`（新規）
またはlegacyの`metadata.date`を優先し、無いときだけ日界ヒューリスティックへ落ちる
（2026-07-26 恒久解。暫定は`is_serina_day_boundary_instant`のみだった）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from serina.core.state.serina_day import SERINA_DAY_HOUR, is_serina_day_boundary_instant

_JST = ZoneInfo("Asia/Tokyo")


def parse_memory_metadata(raw: Any) -> dict[str, Any]:
    """DBのmetadata列（JSON文字列）またはdictをdictへ。壊れていれば空dict。"""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _iso_date_prefix(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return None


def resolve_diary_target_date(
    *,
    created_at: str,
    metadata: dict[str, Any] | None = None,
    boundary_hour: int = SERINA_DAY_HOUR,
) -> str:
    """日記が語る日を YYYY-MM-DD で返す。

    優先順:
    1. `metadata.target_date`（本番日記の明示タグ）
    2. `metadata.date`（legacy投入が焼くキー）
    3. `created_at`がSerina日界の瞬間ちょうど → 1日前（暫定ヒューリスティック）
    4. `created_at`のJST暦日（生成時刻保存の旧形式など）
    5. `created_at`先頭10文字（パース不能時の最終手段）
    """
    meta = metadata or {}
    for key in ("target_date", "date"):
        tagged = _iso_date_prefix(meta.get(key))
        if tagged is not None:
            return tagged

    raw = (created_at or "").strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw[:10]
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(_JST)
    if is_serina_day_boundary_instant(local, boundary_hour=boundary_hour):
        local = local - timedelta(days=1)
    return local.date().isoformat()
