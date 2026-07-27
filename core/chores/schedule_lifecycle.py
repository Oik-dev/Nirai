"""予定・記念日の窓終了後始末（§4.9 v5）。

- 予定: 事後窓を過ぎ、かつ事後が発火済みなら tombstone（物理削除しない）
- 記念日: tombstone せず、発火済みフラグのみ年次リセット
"""

from __future__ import annotations

from datetime import datetime, timezone

from serina.core.chores.schedule_pulse_state import (
    clear_fired_keys_for_fact,
    is_window_fired,
)
from serina.core.context.schedule_window import (
    WINDOW_POST,
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
        if is_schedule_window_open(now, fact) is not None:
            continue
        if not is_window_fired(state, fact.id, WINDOW_POST):
            continue
        # 事後を過ぎ、かつ事後発火済み → tombstone
        fact_store.tombstone_fact(fact.id)
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
