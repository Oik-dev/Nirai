"""予定の tombstone / 記念日年次リセット（Task 1-6b）。"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.chores.schedule_lifecycle import reconcile_schedule_lifecycle
from serina.core.chores.schedule_pulse_state import fired_key
from serina.core.context.schedule_window import WINDOW_EVE, WINDOW_POST, is_schedule_window_open
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.facts import FACT_CATEGORY_ANNIVERSARY, FACT_CATEGORY_SCHEDULE
from serina.core.memory.protection import ChangeLog
from serina.core.memory.store import MemoryStore, RecallParams

JST = ZoneInfo("Asia/Tokyo")


def _fresh_store() -> MemoryStore:
    return MemoryStore(
        str(Path(tempfile.mkdtemp()) / "life.db"),
        embedder=OllamaEmbedder(call_fn=lambda m, t: [1.0, 0.0, 0.0, 0.0]),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def _set_recorded_at(store: MemoryStore, fact_id: str, recorded_at: str) -> None:
    """テスト用: recorded_at を明示上書き（wall clock 依存を避ける）。"""
    conn = store.facts._connect()
    try:
        conn.execute("UPDATE facts SET recorded_at = ? WHERE id = ?", (recorded_at, fact_id))
        conn.commit()
    finally:
        conn.close()


def test_schedule_tombstones_after_post_window(tmp_path: Path) -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="病院",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-28T15:00:00+09:00",
        valid_to="2026-07-28T17:00:00+09:00",
    )
    # 記録は終了前（無条件tombstoneの対象に残す）
    _set_recorded_at(store, fid, "2026-07-28T10:00:00+09:00")
    # 事後閉じ = 20:00。21:00 は全窓クローズ
    now = datetime(2026, 7, 28, 21, 0, tzinfo=JST)
    assert is_schedule_window_open(now, store.facts.get_fact(fid)) is None

    state = {"fired": {fired_key(fid, WINDOW_POST): now.isoformat()}}
    change_log = ChangeLog(tmp_path / "c.jsonl")
    updated = reconcile_schedule_lifecycle(
        now=now, fact_store=store.facts, schedule_pulse_state=state, change_log=change_log,
    )
    fact = store.facts.get_fact(fid)
    assert fact is not None
    assert fact.status == "tombstone"
    assert any(r.action == "予定 tombstone（窓終了）" for r in change_log.read_all())
    # tombstone 時に発火済みフラグもクリア
    assert fired_key(fid, WINDOW_POST) not in updated["fired"]


def test_schedule_tombstones_without_post_fire(tmp_path: Path) -> None:
    """I-2: Pulse 発火の有無に依存せず、窓クローズ＋終了時刻経過で tombstone。"""
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="病院",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-28T15:00:00+09:00",
        valid_to="2026-07-28T17:00:00+09:00",
    )
    _set_recorded_at(store, fid, "2026-07-28T10:00:00+09:00")
    now = datetime(2026, 7, 28, 21, 0, tzinfo=JST)
    updated = reconcile_schedule_lifecycle(
        now=now,
        fact_store=store.facts,
        schedule_pulse_state={"fired": {fired_key(fid, WINDOW_POST): "2026-07-28T18:00:00+09:00"}},
        change_log=ChangeLog(tmp_path / "c.jsonl"),
    )
    assert store.facts.get_fact(fid).status == "tombstone"
    assert fired_key(fid, WINDOW_POST) not in updated["fired"]


def test_schedule_not_tombstone_before_end(tmp_path: Path) -> None:
    """終了前（イベント中など）は窓が閉じていても tombstone しない。"""
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="病院",
        statement="病院",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2026-07-28T15:00:00+09:00",
        valid_to="2026-07-28T17:00:00+09:00",
    )
    # 16:00 = 開始後・終了前。どの窓にも該当しないが end 未経過
    now = datetime(2026, 7, 28, 16, 0, tzinfo=JST)
    assert is_schedule_window_open(now, store.facts.get_fact(fid)) is None
    reconcile_schedule_lifecycle(
        now=now,
        fact_store=store.facts,
        schedule_pulse_state={"fired": {}},
        change_log=ChangeLog(tmp_path / "c.jsonl"),
    )
    assert store.facts.get_fact(fid).status == "active"


def test_anniversary_stays_active_and_resets_flags(tmp_path: Path) -> None:
    store = _fresh_store()
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="has_anniversary",
        object="七夕",
        statement="七夕",
        status="active",
        category=FACT_CATEGORY_ANNIVERSARY,
        episode_ids=[],
        valid_from="--07-07",
    )
    # 7/8 = 記念日開始後・前夜クローズ。フラグを次年に向けてリセット
    now = datetime(2026, 7, 8, 12, 0, tzinfo=JST)
    state = {"fired": {fired_key(fid, WINDOW_EVE): "2026-07-06T19:00:00+09:00"}}
    updated = reconcile_schedule_lifecycle(
        now=now,
        fact_store=store.facts,
        schedule_pulse_state=state,
        change_log=ChangeLog(tmp_path / "c.jsonl"),
    )
    assert store.facts.get_fact(fid).status == "active"
    assert fired_key(fid, WINDOW_EVE) not in updated["fired"]

    # 翌年の前夜がまた開く
    next_eve = datetime(2027, 7, 6, 20, 0, tzinfo=JST)
    assert is_schedule_window_open(next_eve, store.facts.get_fact(fid)) == WINDOW_EVE

def test_date_only_schedule_not_tombstoned_when_recorded_after_end(tmp_path: Path) -> None:
    """時刻なし予定（当日00:00開始→既定2h終了）が、記録時点で既に窓終了でも即tombstoneされない。"""
    store = _fresh_store()
    # 過去の日付のみ予定。add_fact の recorded_at（実行時刻）は end(=02:00) より後になる。
    fid = store.facts.add_fact(
        subject="マスター",
        predicate="has_schedule",
        object="歯医者",
        statement="7月1日に歯医者",
        status="active",
        category=FACT_CATEGORY_SCHEDULE,
        episode_ids=[],
        valid_from="2000-07-01T00:00:00+09:00",
        valid_to=None,
    )
    # 同日夕方 = 全窓クローズかつ end 経過。従来はここで即 tombstone されていた。
    now = datetime(2000, 7, 1, 20, 0, tzinfo=JST)
    assert is_schedule_window_open(now, store.facts.get_fact(fid)) is None
    reconcile_schedule_lifecycle(
        now=now,
        fact_store=store.facts,
        schedule_pulse_state={"fired": {}},
        change_log=ChangeLog(tmp_path / "c.jsonl"),
    )
    assert store.facts.get_fact(fid).status == "active"
