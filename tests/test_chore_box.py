"""宿題箱（ChoreBox）のテスト。設計書v2 §2.4(機会駆動), §2.6(状態目録)。

Phase4スライス1範囲: enqueue/pending/mark_done・永続化（プロセス再起動を模した再オープン）。
消化ロジック（蒸留・日記・機微査定）はPhase4後続スライス。
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core_v2.chores.chore_box import ChoreBox


def _fresh_path() -> Path:
    return Path(tempfile.mkdtemp()) / "test_chore_box.db"


def test_enqueue_and_pending_returns_job_in_order() -> None:
    box = ChoreBox(_fresh_path())

    id1 = box.enqueue("蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "a"}]})
    id2 = box.enqueue("蒸留", lane="cloud", payload={"turns": [{"speaker": "master", "text": "b"}]})

    jobs = box.pending()
    assert [j.id for j in jobs] == [id1, id2]
    assert jobs[0].kind == "蒸留"
    assert jobs[0].lane == "local"
    assert jobs[0].payload == {"turns": [{"speaker": "master", "text": "a"}]}
    assert jobs[1].lane == "cloud"


def test_mark_done_removes_job() -> None:
    box = ChoreBox(_fresh_path())
    job_id = box.enqueue("蒸留", lane="local", payload={"x": 1})

    box.mark_done(job_id)

    assert box.pending() == []
    assert box.count() == 0


def test_pending_filters_by_kind() -> None:
    box = ChoreBox(_fresh_path())
    box.enqueue("蒸留", lane="local", payload={})
    box.enqueue("日記", lane="local", payload={})

    diary_jobs = box.pending(kind="日記")

    assert len(diary_jobs) == 1
    assert diary_jobs[0].kind == "日記"


def test_pending_respects_limit() -> None:
    box = ChoreBox(_fresh_path())
    for i in range(5):
        box.enqueue("蒸留", lane="local", payload={"i": i})

    jobs = box.pending(limit=2)

    assert len(jobs) == 2


def test_survives_reopen_electrical_shutdown_style() -> None:
    """§2.4: 宿題箱はディスクに永続化し、電源断・強制終了に耐える（宿題は消えず回収される）。"""
    path = _fresh_path()
    box1 = ChoreBox(path)
    box1.enqueue("蒸留", lane="local", payload={"turns": []})

    box2 = ChoreBox(path)  # プロセス再起動を模した再オープン

    assert box2.count() == 1


def main() -> None:
    tests = [
        test_enqueue_and_pending_returns_job_in_order,
        test_mark_done_removes_job,
        test_pending_filters_by_kind,
        test_pending_respects_limit,
        test_survives_reopen_electrical_shutdown_style,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  [OK] {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  [NG] {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  [NG] {t.__name__}: 予期せぬ例外 {type(e).__name__}: {e}")
    if failed == 0:
        print("全テスト合格")
    else:
        print(f"{failed}件 失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
