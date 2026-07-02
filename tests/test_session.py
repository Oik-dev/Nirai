"""3a セッション基盤の自動アサーションテスト（Ollama不要）"""

from __future__ import annotations

import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.config import CoreConfig
from serina.core.session import SessionManager, living_date
from serina.memory.store import MemoryStore

# Windowsコンソール(cp932)でも ✓/✗ が出力できるようにする
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

JST = timezone(timedelta(hours=9))


class _FakeEmbedder:
    def embed(self, text: str) -> list[float]:
        return [0.0] * 1024


def _fresh_store() -> MemoryStore:
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    return MemoryStore(_FakeEmbedder(), db_path=tmp)


def test_timeout_6h() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, CoreConfig(), tz=JST)
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    sid1, pending = mgr.resolve_active_session(now)
    assert pending is None
    # 最終発言を7時間前に偽装
    store.touch_session_activity(sid1, (now - timedelta(hours=7)).isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending == sid1, "旧セッションがpending化されていない"
    assert sid2 != sid1, "新セッションが作られていない"
    assert store.get_active_session()["id"] == sid2


def test_living_date_am4() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, CoreConfig(), tz=JST)
    now = datetime(2026, 7, 1, 5, 0, tzinfo=JST)  # JST 05:00 → 生活日=7/1
    sid1, _ = mgr.resolve_active_session(now)
    # 前回発言を同日JST AM3:30（＝生活日6/30）に偽装。経過1.5h(<6h)でも生活日が違う
    last = datetime(2026, 7, 1, 3, 30, tzinfo=JST)
    store.touch_session_activity(sid1, last.isoformat())
    assert living_date(now, 4, JST) != living_date(last, 4, JST)
    sid2, pending = mgr.resolve_active_session(now)
    assert pending == sid1, "AM4:00日界を跨いだのに新セッションにならない"
    assert sid2 != sid1


def test_living_date_uses_local_tz() -> None:
    """UTC保存のタイムスタンプでもJSTの生活日で判定される（昼13時分断バグの回帰テスト）"""
    store = _fresh_store()
    mgr = SessionManager(store, CoreConfig(), tz=JST)
    now = datetime(2026, 7, 1, 4, 30, tzinfo=timezone.utc)  # JST 13:30 → 生活日7/1
    sid1, _ = mgr.resolve_active_session(now)
    # 1.5時間前＝JST 12:00。UTC日付のまま−4hだと日界(UTC04:00)を跨ぐが、生活日は同じ7/1
    last = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    store.touch_session_activity(sid1, last.isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending is None, "UTCベースの生活日計算で昼13時に誤分断された"
    assert sid2 == sid1


def test_stay_active() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, CoreConfig(), tz=JST)
    now = datetime(2026, 7, 1, 15, 0, tzinfo=timezone.utc)
    sid1, _ = mgr.resolve_active_session(now)
    store.touch_session_activity(sid1, (now - timedelta(hours=1)).isoformat())
    sid2, pending = mgr.resolve_active_session(now)
    assert pending is None, "継続すべきセッションを誤ってpending化した"
    assert sid2 == sid1, "継続すべきなのに新セッションを作った"


def test_archive_move() -> None:
    store = _fresh_store()
    mgr = SessionManager(store, CoreConfig())
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
    mgr = SessionManager(store, CoreConfig())
    sid, _ = mgr.resolve_active_session()
    store.add_history(sid, "user", "古い発言")
    store.archive_session_history(sid)
    # archived_atは現在時刻なので、days=0 で全件対象
    assert store.purge_archived_older_than(0) >= 1
    assert store.count_archived() == 0


def main() -> None:
    tests = [
        test_timeout_6h,
        test_living_date_am4,
        test_living_date_uses_local_tz,
        test_stay_active,
        test_archive_move,
        test_purge,
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
