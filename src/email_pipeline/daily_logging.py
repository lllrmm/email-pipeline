"""Logging handler that writes into device-local date component folders."""

from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo


DEFAULT_LOG_LEVEL = logging.INFO
VALID_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def parse_log_level(value: object, default: int = DEFAULT_LOG_LEVEL) -> int:
    text = str(value or "").strip().upper()
    if not text:
        return default
    level = VALID_LOG_LEVELS.get(text)
    if level is None:
        print(f"warning: invalid log level '{value}' (using {logging.getLevelName(default)})", file=sys.stderr)
        return default
    return level


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


class LevelStreamHandler(logging.Handler):
    """Write console records: ERROR and above to stderr, everything else to stdout."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            stream = sys.stderr if record.levelno >= logging.ERROR else sys.stdout
            stream.write(self.format(record) + "\n")
            stream.flush()
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


def configure_daily_logger(output_root: Path, component: str, timezone_name: str, file_level: int = DEFAULT_LOG_LEVEL, console_level: int | None = None) -> logging.Logger:
    console = file_level if console_level is None else console_level
    timezone = ZoneInfo(timezone_name)
    logger = logging.getLogger(f"email_pipeline.{component}")
    logger.setLevel(min(file_level, console))
    logger.handlers.clear()
    logger.propagate = False
    handler = LocalDailyFileHandler(output_root, component, timezone)
    formatter = ZonedFormatter(timezone)
    handler.setFormatter(formatter)
    handler.setLevel(file_level)
    logger.addHandler(handler)
    console_handler = LevelStreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.setLevel(console)
    logger.addHandler(console_handler)
    return logger


def configure_run_logger(output_root: Path, component: str, timezone_name: str, file_level: int = DEFAULT_LOG_LEVEL, console_level: int | None = None) -> logging.Logger:
    """Configure a logger writing one immutable file for this process run."""
    console = file_level if console_level is None else console_level
    timezone = ZoneInfo(timezone_name)
    logger = logging.getLogger(f"email_pipeline.{component}")
    logger.setLevel(min(file_level, console))
    for old_handler in logger.handlers:
        old_handler.close()
        old_path = getattr(old_handler, "baseFilename", None)
        if old_path:
            try:
                Path(old_path).unlink()
            except FileNotFoundError:
                pass
    logger.handlers.clear()
    logger.propagate = False
    now = dt.datetime.now(timezone)
    run_id = f"{now.strftime('%Y%m%dT%H%M%S%z')}-{os.getpid()}"
    logs_root = output_root.expanduser().resolve() / now.date().isoformat() / "logs" / component
    logs_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs_root.chmod(0o700)
    path = logs_root / f"{component}-{run_id}.log"
    path.touch(mode=0o600, exist_ok=False)
    path.chmod(0o600)
    handler = logging.FileHandler(path, encoding="utf-8", delay=True)
    formatter = ZonedFormatter(timezone)
    handler.setFormatter(formatter)
    handler.setLevel(file_level)
    logger.addHandler(handler)
    console_handler = LevelStreamHandler()
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.setLevel(console)
    logger.addHandler(console_handler)
    return logger
