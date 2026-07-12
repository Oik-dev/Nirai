"""セッションのライフサイクル判断（Core層：決めるだけ）"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone, tzinfo

from serina.core.config import CoreConfig
from serina.memory.store import MemoryStore


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def living_date(dt: datetime, offset_hours: int, tz: tzinfo | None = None) -> date:
    """生活日 = (ローカル時刻 − offset_hours) の日付。AM offset_hours が日界になる。

    DBのタイムスタンプはUTC保存のため、必ずローカル時刻（tz=NoneならシステムTZ）へ
    変換してから判定する。UTCのまま計算すると日界がJST 13:00にずれる。
    """
    return (dt.astimezone(tz) - timedelta(hours=offset_hours)).date()


class SessionManager:
    """起動時に『どの active セッションで話すか』を決める。"""

    def __init__(
        self,
        store: MemoryStore,
        config: CoreConfig | None = None,
        tz: tzinfo | None = None,
    ) -> None:
        self.store = store
        self.config = config or CoreConfig()
        self.tz = tz  # Noneならシステムローカル（生活日判定に使用）

    def _new_session_id(self, now: datetime) -> str:
        return f"s_{now.strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"

    def resolve_active_session(
        self, now: datetime | None = None
    ) -> tuple[str, str | None]:
        """使うべき active セッションIDと、pending化した旧セッションID（無ければNone）を返す。"""
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
        """セッション区切り（無操作タイムアウト・明示の別れの挨拶）で帳簿を回転する
        （2026-07-12決定・軽微: 区切り時にGUI帳簿のセッションIDも回転させないと、
        アイドル終了後の再開会話が同じ履歴行に積まれ続けてしまう。DECISIONS参照）。

        現在のセッションを"pending"化し、新しいアクティブセッションIDを発行して返す。
        """
        now = now or datetime.now(timezone.utc)
        self.store.set_session_status(current_session_id, "pending")
        sid = self._new_session_id(now)
        self.store.create_session(sid, now.isoformat())
        return sid
