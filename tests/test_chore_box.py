"""宿題箱（ChoreBox）のテスト。設計書 §2.4(機会駆動), §2.6(状態目録)。

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

from serina.core.chores.chore_box import ChoreBox


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


def test_increment_failure_returns_updated_count() -> None:
    box = ChoreBox(_fresh_path())
    job_id = box.enqueue("蒸留", lane="cloud", payload={})

    assert box.increment_failure(job_id) == 1
    assert box.increment_failure(job_id) == 2
    assert box.pending()[0].failure_count == 2


def test_switch_lane_updates_lane_and_resets_failure_count() -> None:
    box = ChoreBox(_fresh_path())
    job_id = box.enqueue("蒸留", lane="cloud", payload={})
    box.increment_failure(job_id)
    box.increment_failure(job_id)

    box.switch_lane(job_id, "local")

    job = box.pending()[0]
    assert job.lane == "local"
    assert job.failure_count == 0


def test_shelve_moves_job_out_of_pending_and_into_shelf() -> None:
    box = ChoreBox(_fresh_path())
    job_id = box.enqueue("蒸留", lane="local", payload={"turns": []})

    box.shelve(job_id, reason="3回連続失敗のため棚上げ")

    assert box.pending() == []
    assert box.shelved_count() == 1
    shelved = box.shelved()
    assert shelved[0].id == job_id
    assert shelved[0].reason == "3回連続失敗のため棚上げ"


def test_note_assessment_failure_and_shelve() -> None:
    box = ChoreBox(_fresh_path())

    assert box.note_assessment_failure(42) == 1
    assert box.note_assessment_failure(42) == 2
    assert box.shelved_assessment_ids() == set()

    box.shelve_assessment(42, reason="3回連続失敗のため棚上げ")

    assert box.shelved_assessment_ids() == {42}
    assert box.shelved_assessment_count() == 1


def test_note_assessment_failure_stores_reason() -> None:
    box = ChoreBox(_fresh_path())

    box.note_assessment_failure(7, reason="AssessmentParseError: gradeフィールドが無い")

    assert "grade" in (box.assessment_failure_reason(7) or "")


def test_unshelve_assessments_clears_shelf_and_failure_count() -> None:
    box = ChoreBox(_fresh_path())
    box.note_assessment_failure(7, reason="parse fail")
    box.note_assessment_failure(7, reason="parse fail")
    box.note_assessment_failure(7, reason="parse fail")
    box.shelve_assessment(7, reason="3回連続失敗のため棚上げ")

    assert box.unshelve_assessments([7]) == [7]
    assert box.shelved_assessment_ids() == set()
    assert box.assessment_failure_reason(7) is None


def test_dismiss_shelved_removes_job_from_shelf() -> None:
    """2026-08-01: 発言削除カスケードで棚上げ済みジョブの残骸も除去できること。"""
    box = ChoreBox(_fresh_path())
    job_id = box.enqueue("蒸留", lane="local", payload={"turns": []})
    box.shelve(job_id, reason="3回連続失敗のため棚上げ")
    assert box.shelved_count() == 1

    box.dismiss_shelved(job_id)

    assert box.shelved_count() == 0
    assert box.shelved() == []


def test_clear_assessment_failure_removes_entry_and_returns_true() -> None:
    """2026-08-01: 記憶の物理削除に伴い、死んだmemory_idの査定失敗台帳を残さない。"""
    box = ChoreBox(_fresh_path())
    box.note_assessment_failure(99, reason="parse fail")

    assert box.clear_assessment_failure(99) is True
    assert box.assessment_failure_reason(99) is None
    assert box.clear_assessment_failure(99) is False


def main() -> None:
    tests = [
        test_enqueue_and_pending_returns_job_in_order,
        test_mark_done_removes_job,
        test_pending_filters_by_kind,
        test_pending_respects_limit,
        test_survives_reopen_electrical_shutdown_style,
        test_increment_failure_returns_updated_count,
        test_switch_lane_updates_lane_and_resets_failure_count,
        test_shelve_moves_job_out_of_pending_and_into_shelf,
        test_note_assessment_failure_and_shelve,
        test_note_assessment_failure_stores_reason,
        test_unshelve_assessments_clears_shelf_and_failure_count,
        test_dismiss_shelved_removes_job_from_shelf,
        test_clear_assessment_failure_removes_entry_and_returns_true,
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
