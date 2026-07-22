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
