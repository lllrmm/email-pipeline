"""Logging handler that writes into UTC date-partitioned component folders."""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path


class UtcDailyFileHandler(logging.Handler):
    def __init__(self, output_root: Path, component: str) -> None:
        super().__init__()
        self.output_root = output_root.expanduser().resolve()
        self.component = component

    def emit(self, record: logging.LogRecord) -> None:
        try:
            day = dt.datetime.now(dt.timezone.utc).date().isoformat()
            directory = self.output_root / day / "logs" / self.component
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)
            path = directory / f"{self.component}.log"
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(self.format(record) + "\n")
            path.chmod(0o600)
        except Exception:
            self.handleError(record)


def configure_daily_logger(output_root: Path, component: str) -> logging.Logger:
    logger = logging.getLogger(f"email_pipeline.{component}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    handler = UtcDailyFileHandler(output_root, component)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = __import__("time").gmtime
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger
