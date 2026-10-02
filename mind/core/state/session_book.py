"""GUI帳簿セッションの寿命判断（どの session_id で話すか）。

`state/session.py` の SessionState（進行中会話の原文＋要約）とは別物。
こちらは DB 上の sessions 台帳の回転・タイムアウト・生活日境界のみを扱う。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo

from serina.core.memory.session_store import SessionStore


@dataclass(frozen=True)
class SessionBookConfig:
    """帳簿セッション寿命のツマミ（ThresholdsConfig とは区画を分ける）。"""

    session_timeout_hours: float = 6.0
    living_date_offset_hours: int = 4


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def living_date(dt: datetime, offset_hours: int, tz: tzinfo | None = None) -> date:
    """生活日 = (ローカル時刻 − offset_hours) の日付。AM offset_hours が日界になる。

    DBのタイムスタンプはUTC保存のため、必ずローカル時刻（tz=NoneならシステムTZ）へ
    変換してから判定する。UTCのまま計算すると日界がずれる。
    """
    return (dt.astimezone(tz) - timedelta(hours=offset_hours)).date()


class SessionManager:
    """起動時に『どの active セッションで話すか』を決める。"""

    def __init__(
        self,
        store: SessionStore,
        config: SessionBookConfig | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.store = store
        self.config = config or SessionBookConfig()
        self.tz = tz

    def _new_session_id(self, now: datetime) -> str:
        return f"s_{now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

    def resolve_active_session(
        self, now: datetime | None = None
    ) -> tuple[str, str | None]:
        """使うべき active セッションIDと、pending化した旧セッションID（無ければNone）。"""
        now = now or datetime.now(timezone.utc)
        active = self.store.get_active_session()

        if active is None:
            sid = self._new_session_id(now)
            self.store.create_session(sid, now.isoformat())
            return sid, None

        last = _parse_iso(active["last_activity"])
        elapsed_h = (now - last).total_seconds() / 3600.0
        offset = self.config.living_date_offset_hours
        timed_out = (
            elapsed_h >= self.config.session_timeout_hours
            or living_date(now, offset, self.tz) != living_date(last, offset, self.tz)
        )

        if timed_out:
            self.store.set_session_status(active["id"], "pending")
            sid = self._new_session_id(now)
            self.store.create_session(sid, now.isoformat())
            return sid, active["id"]

        return active["id"], None

    def rotate(self, current_session_id: str, *, now: datetime | None = None) -> str:
        """セッション区切りで帳簿を回転する。現セッションを pending 化し新 active を返す。"""
        now = now or datetime.now(timezone.utc)
        self.store.set_session_status(current_session_id, "pending")
        sid = self._new_session_id(now)
        self.store.create_session(sid, now.isoformat())
        return sid
