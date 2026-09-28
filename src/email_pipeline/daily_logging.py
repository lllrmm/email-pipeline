"""Logging handler that writes into device-local date component folders."""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo


class LocalDailyFileHandler(logging.Handler):
    def __init__(self, output_root: Path, component: str, timezone: ZoneInfo) -> None:
        super().__init__()
        self.output_root = output_root.expanduser().resolve()
        self.component = component
        self.timezone = timezone

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = dt.datetime.now(self.timezone).date().isoformat()
            logs_root = self.output_root / day / "logs"
            logs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            logs_root.chmod(0o700)
            directory = logs_root / self.component
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            path = directory / f"{self.component}.log"
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(self.format(record) + "\n")
            path.chmod(0o600)
        except Exception:
            self.handleError(record)


class ZonedFormatter(logging.Formatter):
    def __init__(self, timezone: ZoneInfo) -> None:
        super().__init__("%(asctime)s %(levelname)s %(name)s %(message)s")
        self.timezone = timezone
        self.default_msec_format = None

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        value = dt.datetime.fromtimestamp(record.created, self.timezone)
        return value.isoformat(timespec="seconds")


def configure_daily_logger(output_root: Path, component: str, timezone_name: str) -> logging.Logger:
    timezone = ZoneInfo(timezone_name)
    logger = logging.getLogger(f"email_pipeline.{component}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = LocalDailyFileHandler(output_root, component, timezone)
    formatter = ZonedFormatter(timezone)
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
