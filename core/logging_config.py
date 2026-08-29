from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
from typing import TextIO


LOGGER_NAME = "nirai.core"


class IsoLocalFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created).astimezone().isoformat(timespec="seconds")


class DailyCoreFileHandler(logging.Handler):
    def __init__(self, logs_dir: Path) -> None:
        super().__init__()
        self.logs_dir = logs_dir
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._date_key: str | None = None
        self._stream: TextIO | None = None

    def _ensure_stream(self, record: logging.LogRecord) -> TextIO:
        date_key = datetime.fromtimestamp(record.created).astimezone().strftime("%Y%m%d")
        if self._stream is not None and self._date_key == date_key:
            return self._stream

        if self._stream is not None:
            self._stream.close()

        path = self.logs_dir / f"core-{date_key}.log"
        self._stream = path.open("a", encoding="utf-8", newline="\n", buffering=1)
        self._date_key = date_key
        return self._stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = self._ensure_stream(record)
            stream.write(self.format(record) + "\n")
            stream.flush()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            if self._stream is not None:
                self._stream.close()
                self._stream = None
                self._date_key = None
        finally:
            super().close()


def _level_from_name(name: str) -> int:
    return {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARN": logging.WARNING,
        "ERROR": logging.ERROR,
    }.get(name.upper(), logging.INFO)


def configure_core_logging(root: Path, level_name: str) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(_level_from_name(level_name))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = DailyCoreFileHandler(root / "runtime" / "logs")
    handler.setLevel(logger.level)
    handler.setFormatter(
        IsoLocalFormatter("[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s")
    )
    logger.addHandler(handler)
    logger.info("logging_ready level=%s", level_name.upper())
    return logger


def shutdown_core_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
