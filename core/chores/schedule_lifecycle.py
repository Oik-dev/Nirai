"""予定・記念日の窓終了後始末（§4.9 v5）。

- 予定: 全窓が閉じ、かつ終了時刻を過ぎていれば無条件 tombstone（Pulse 発火に依存しない）
- 記念日: tombstone せず、発火済みフラグのみ年次リセット
"""

from __future__ import annotations

from datetime import datetime, timezone

from serina.core.chores.schedule_pulse_state import clear_fired_keys_for_fact
from serina.core.context.schedule_window import (
    is_schedule_window_open,
    resolve_schedule_bounds,
)
from serina.core.memory.facts import (
    FACT_CATEGORY_ANNIVERSARY,
    FACT_CATEGORY_SCHEDULE,
    FactStore,
)
from serina.core.memory.protection import ChangeLog, ChangeReport


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def reconcile_schedule_lifecycle(
    *,
    now: datetime,
    fact_store: FactStore,
    schedule_pulse_state: dict,
    change_log: ChangeLog | None = None,
) -> dict:
    """窓終了後の tombstone / 記念日フラグリセット。更新後の state を返す。"""
    state = {
        "fired": dict((schedule_pulse_state or {}).get("fired") or {}),
    }
    local_now = now.astimezone() if now.tzinfo else now

    schedules = fact_store.list_active_facts_by_category(FACT_CATEGORY_SCHEDULE)
    for fact in schedules:
        # §4.9: 窓がすべて閉じた予定は無条件で tombstone（Pulse 発火の有無に依存しない）
        if is_schedule_window_open(now, fact) is not None:
            continue
        bounds = resolve_schedule_bounds(fact, now)
        if bounds is None:
            continue
        _start, end = bounds
        if local_now <= end:
            continue
        # 記録時点で既に窓が終わっていた予定は tombstone しない
        # （日付のみ抽出→当日00:00開始→既定2h終了、が夕方登録で即消えるのを防ぐ）
        if fact.recorded_at:
            try:
                recorded = datetime.fromisoformat(fact.recorded_at)
                if recorded.tzinfo is None and end.tzinfo is not None:
                    recorded = recorded.replace(tzinfo=timezone.utc)
                elif recorded.tzinfo is not None and end.tzinfo is None:
                    recorded = recorded.replace(tzinfo=None)
                if recorded > end:
                    continue
            except (ValueError, TypeError):
                pass
        fact_store.tombstone_fact(fact.id)
        state = clear_fired_keys_for_fact(state, fact.id)
        report = ChangeReport(
            timestamp=_utc_now_iso(),
            action="予定 tombstone（窓終了）",
            target_id=0,
            reason=f"事後窓を過ぎたため tombstone（fact_id={fact.id}）",
            before=fact.statement,
            after=None,
        )
        if change_log is not None:
            change_log.record(report)

    anniversaries = fact_store.list_active_facts_by_category(FACT_CATEGORY_ANNIVERSARY)
    for fact in anniversaries:
        if is_schedule_window_open(now, fact) is not None:
            continue
        bounds = resolve_schedule_bounds(fact, now)
        if bounds is None:
            continue
        start, _end = bounds
        # 今年の記念日開始を過ぎていれば、次年に向けて発火済みをリセット
        if local_now < start:
            continue
        fired = state.get("fired") or {}
        if not any(str(k).startswith(f"{fact.id}:") for k in fired):
            continue
        state = clear_fired_keys_for_fact(state, fact.id)
        report = ChangeReport(
            timestamp=_utc_now_iso(),
            action="記念日発火フラグ年次リセット",
            target_id=0,
            reason=f"fact_id={fact.id} の発火済みを次年に向けてクリア",
            before=fact.statement,
            after=None,
        )
        if change_log is not None:
            change_log.record(report)

    return state
