from __future__ import annotations

from datetime import datetime, timedelta
import logging
from pathlib import Path

from core.logging_config import (
    DailyCoreFileHandler,
    IsoLocalFormatter,
    configure_core_logging,
    shutdown_core_logging,
)


def test_configured_core_logger_writes_utf8_operational_log(tmp_path: Path) -> None:
    configure_core_logging(tmp_path, "INFO")
    try:
        logger = logging.getLogger("nirai.core.test")
        logger.info("brain_success request_id=REQ-1 invocation_id=INV-1")
    finally:
        shutdown_core_logging()

    date_key = datetime.now().astimezone().strftime("%Y%m%d")
    log_path = tmp_path / "runtime" / "logs" / f"core-{date_key}.log"
    text = log_path.read_text(encoding="utf-8")

    assert "[INFO] [nirai.core.test] brain_success request_id=REQ-1 invocation_id=INV-1" in text
    assert text.startswith("[")


def test_daily_handler_switches_files_when_record_date_changes(tmp_path: Path) -> None:
    logs_dir = tmp_path / "logs"
    handler = DailyCoreFileHandler(logs_dir)
    handler.setFormatter(IsoLocalFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"))
    try:
        first = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
        second = first + timedelta(days=1)
        for when, message in ((first, "first-day"), (second, "second-day")):
            record = logging.LogRecord(
                name="nirai.core.test",
                level=logging.INFO,
                pathname=__file__,
                lineno=1,
                msg=message,
                args=(),
                exc_info=None,
            )
            record.created = when.timestamp()
            handler.handle(record)
    finally:
        handler.close()

    first_path = logs_dir / f"core-{first.strftime('%Y%m%d')}.log"
    second_path = logs_dir / f"core-{second.strftime('%Y%m%d')}.log"
    assert "first-day" in first_path.read_text(encoding="utf-8")
    assert "second-day" in second_path.read_text(encoding="utf-8")
