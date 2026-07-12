"""セッション帳簿の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.session_store import SessionStore
from serina.core.state.session_book import SessionBookConfig, SessionManager, living_date

# Windowsコンソール(cp932)でも ✓/✗ が出力できるようにする
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JST = timezone(timedelta(hours=9))


def _fresh_store() -> SessionStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return SessionStore(tmp)


def test_timeout_6h() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig(), tz=JST)
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    sid1, pending = mgr.resolve_active_session(now)
    assert pending is None
    store.touch_session_activity(sid1, (now - timedelta(hours=7)).isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending == sid1, "旧セッションがpending化されていない"
    assert sid2 != sid1, "新セッションが作られていない"
    assert store.get_active_session()["id"] == sid2


def test_living_date_am4() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig(), tz=JST)
    now = datetime(2026, 7, 1, 5, 0, tzinfo=JST)
    sid1, _ = mgr.resolve_active_session(now)
    last = datetime(2026, 7, 1, 3, 30, tzinfo=JST)
    store.touch_session_activity(sid1, last.isoformat())
    assert living_date(now, 4, JST) != living_date(last, 4, JST)
    sid2, pending = mgr.resolve_active_session(now)
    assert pending == sid1, "AM4:00日界を跨いだのに新セッションにならない"
    assert sid2 != sid1


def test_living_date_uses_local_tz() -> None:
    """UTC保存のタイムスタンプでもJSTの生活日で判定される"""
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig(), tz=JST)
    now = datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc)
    sid1, _ = mgr.resolve_active_session(now)
    last = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    store.touch_session_activity(sid1, last.isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending is None, "UTCベースの生活日計算で昼13時に誤分断された"
    assert sid2 == sid1


def test_stay_active() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig(), tz=JST)
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    sid1, _ = mgr.resolve_active_session(now)
    store.touch_session_activity(sid1, (now - timedelta(hours=1)).isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending is None, "継続すべきセッションを誤ってpending化した"
    assert sid2 == sid1, "継続すべきなのに新セッションを作った"


def test_archive_move() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig())
    sid, _ = mgr.resolve_active_session()
    store.add_history(sid, "user", "こんにちは")
    store.add_history(sid, "assistant", "やあ、マスター")
    moved = store.archive_session_history(sid)
    assert moved == 2, f"退避件数が想定外: {moved}"
    assert store.get_recent_history(sid, 10) == [], "historyから消えていない"
    assert store.count_archived(sid) == 2, "archived_historyへ移動していない"
    store.set_session_status(sid, "distilled", datetime.now(timezone.utc).isoformat())
    assert store.get_active_session() is None, "distilled後にactiveが残っている"


def test_purge() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig())
    sid, _ = mgr.resolve_active_session()
    store.add_history(sid, "user", "古い発言")
    store.archive_session_history(sid)
    assert store.purge_archived_older_than(0) >= 1
    assert store.count_archived() == 0


def test_list_sessions_and_archived_read() -> None:
    """GUI用の読み取りAPI（一覧・蒸留済み閲覧）"""
    store = _fresh_store()
    now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    store.create_session("s_old", now.isoformat())
    store.create_session("s_new", (now + timedelta(hours=1)).isoformat())
    sessions = store.list_sessions()
    assert [s["id"] for s in sessions] == ["s_new", "s_old"], \
        f"last_activity降順になっていない: {[s['id'] for s in sessions]}"

    store.add_history("s_old", "user", "昔の話")
    store.add_history("s_old", "assistant", "覚えてるよ")
    store.archive_session_history("s_old")
    archived = store.get_archived_history("s_old")
    assert [m["content"] for m in archived] == ["昔の話", "覚えてるよ"], \
        "archived_historyを時系列で読めない"
    assert store.get_archived_history("s_new") == [], "無関係セッションが混入"


def test_rotate_marks_current_pending_and_returns_new_active_session() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, SessionBookConfig())
    now = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
    sid1, _ = mgr.resolve_active_session(now)

    sid2 = mgr.rotate(sid1, now=now + timedelta(minutes=1))

    assert sid2 != sid1
    assert store.get_active_session()["id"] == sid2
    sessions_by_id = {s["id"]: s for s in store.list_sessions()}
    assert sessions_by_id[sid1]["status"] == "pending"


def main() -> None:
    tests = [
        test_timeout_6h,
        test_living_date_am4,
        test_living_date_uses_local_tz,
        test_stay_active,
        test_archive_move,
        test_purge,
        test_list_sessions_and_archived_read,
        test_rotate_marks_current_pending_and_returns_new_active_session,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
