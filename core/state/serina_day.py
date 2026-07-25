"""Serina日（07:00 境）の判定ヘルパ。設計書 §2.4 Serina日とセッション切替。

暦日 0:00 ではなく、ローカル時刻の boundary_hour（既定 07:00）を日界とする。
深夜〜早朝の会話は前の Serina 日に属する。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo


SERINA_DAY_HOUR = 7


def serina_day_id(dt: datetime, *, boundary_hour: int = SERINA_DAY_HOUR) -> date:
    """ローカル時刻で Serina 日の date を返す。boundary_hour 未満なら前日。"""
    local = dt.astimezone()
    if local.hour < boundary_hour:
        return local.date() - timedelta(days=1)
    return local.date()


def serina_day_start(
    day: date,
    *,
    boundary_hour: int = SERINA_DAY_HOUR,
    tz: tzinfo | None = None,
) -> datetime:
    """指定 Serina 日が始まる瞬間（UTC）。"""
    if tz is None:
        tz = datetime.now().astimezone().tzinfo or timezone.utc
    local = datetime.combine(day, time(boundary_hour, 0), tzinfo=tz)
    return local.astimezone(timezone.utc)


def is_serina_day_boundary_instant(
    local: datetime, *, boundary_hour: int = SERINA_DAY_HOUR,
) -> bool:
    """`local`がちょうどSerina日界の瞬間（例07:00:00.000000）かどうか。

    `serina_day_start`が返す値は`time(boundary_hour, 0)`から組み立てるため必ずこの形に
    なる。日記(episodic)の`created_at`を「対象Serina日の終わり」で保存したかどうかを
    表示層が見分ける目印として使う（`core/chores/diary.py`参照）。
    `local`は呼び出し側でboundary_hourの基準となるローカル時刻（例JST）へ変換済みで渡すこと。

    分秒0だけでなく`hour == boundary_hour`まで絞るのは、legacy投入記憶（旧日記）が
    時刻不明時のデフォルト値として`12:00:00`ちょうどを大量に使っており（実測: 28件中
    13件）、分秒のみの判定だとそれらを誤って境界揃えと判定してしまうため
    （2026-07-26是正: serina-code-reviewer指摘C-1）。
    """
    return (
        local.hour == boundary_hour
        and local.minute == 0
        and local.second == 0
        and local.microsecond == 0
    )


def crossed_serina_day_boundary(
    prev: datetime,
    now: datetime,
    *,
    boundary_hour: int = SERINA_DAY_HOUR,
) -> bool:
    """prev から now の間に Serina 日界を跨いだか。"""
    return serina_day_id(prev, boundary_hour=boundary_hour) != serina_day_id(
        now, boundary_hour=boundary_hour
    )


def should_run_day_boundary(
    *,
    now: datetime,
    last_activity_at: datetime,
    last_boundary_serina_day: date | None,
    grace_seconds: float = 900,
    boundary_hour: int = SERINA_DAY_HOUR,
) -> bool:
    """日界処理（蒸留→日記→セッション切替）を走らせるべきか。

    - いまの Serina 日が last_boundary_serina_day より新しい
    - かつ最終発言から grace_seconds 以上経過（会話中は延期）
    - last_boundary_serina_day が None なら「今日分はまだ未処理」として True
      （grace のみ適用。起動時朝礼との統合を想定）
    """
    if (now - last_activity_at).total_seconds() < grace_seconds:
        return False

    current_day = serina_day_id(now, boundary_hour=boundary_hour)
    if last_boundary_serina_day is None:
        return True
    return current_day > last_boundary_serina_day
