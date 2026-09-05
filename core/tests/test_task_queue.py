from __future__ import annotations

from pathlib import Path

import pytest

from core.task_queue import (
    QueuedTaskRecord,
    TASK_QUEUE_PENDING_LIMIT,
    TaskQueueState,
    TaskQueueStore,
    TaskQueueStoreError,
)


def _record(tmp_path: Path, task_id: str, *, target: str | None = None) -> QueuedTaskRecord:
    metadata = tmp_path / "runtime" / "workspace" / task_id
    working = tmp_path / "projects" / target if target else metadata
    return QueuedTaskRecord(
        task_id=task_id,
        text=f"work {task_id}",
        message_id=f"REQ-{task_id}",
        origin_session_id="S-1",
        working_dir=str(working),
        task_metadata_dir=str(metadata),
        target_name=target,
    )


def test_task_queue_store_round_trips_active_and_fifo_pending(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    active = _record(tmp_path, "TASK-A")
    pending = [
        _record(tmp_path, "TASK-B", target="ProjectA"),
        _record(tmp_path, "TASK-C"),
    ]

    store.save(active=active, pending=pending)
    state = store.load()

    assert state == TaskQueueState(active=active, pending=tuple(pending))
    raw = store.path.read_text(encoding="utf-8")
    assert raw.index("TASK-B") < raw.index("TASK-C")


def test_task_queue_store_rejects_duplicate_task_ids(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    record = _record(tmp_path, "TASK-DUP")

    with pytest.raises(TaskQueueStoreError, match="duplicate"):
        store.save(active=record, pending=[record])


def test_task_queue_store_rejects_oversized_task_text_before_persisting(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    record = QueuedTaskRecord(
        task_id="TASK-LARGE",
        text="x" * 32_001,
        message_id=None,
        origin_session_id="S-1",
        working_dir=str(tmp_path / "runtime" / "workspace" / "TASK-LARGE"),
        task_metadata_dir=str(tmp_path / "runtime" / "workspace" / "TASK-LARGE"),
    )

    with pytest.raises(TaskQueueStoreError, match="exceeds"):
        store.save(active=record, pending=[])

    assert store.path.exists() is False


def test_task_queue_store_rejects_pending_count_over_limit(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    pending = [
        _record(tmp_path, f"TASK-{index}")
        for index in range(TASK_QUEUE_PENDING_LIMIT + 1)
    ]

    with pytest.raises(TaskQueueStoreError, match="configured limit"):
        store.save(active=None, pending=pending)

    assert store.path.exists() is False


def test_task_queue_store_wraps_parent_directory_write_failure(tmp_path: Path) -> None:
    (tmp_path / "runtime").write_text("not a directory", encoding="utf-8")
    store = TaskQueueStore(tmp_path)

    with pytest.raises(TaskQueueStoreError, match="could not be saved"):
        store.save(active=None, pending=[])


def test_task_queue_store_rejects_corrupt_state_without_overwriting_it(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text('{"format_version":1,"active":', encoding="utf-8")
    before = store.path.read_bytes()

    with pytest.raises(TaskQueueStoreError, match="could not be read"):
        store.load()

    assert store.path.read_bytes() == before


def test_task_queue_store_rejects_invalid_utf8_without_escaping_restore_boundary(tmp_path: Path) -> None:
    store = TaskQueueStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_bytes(b"\xff\xfe\xfd")

    with pytest.raises(TaskQueueStoreError, match="could not be read"):
        store.load()
