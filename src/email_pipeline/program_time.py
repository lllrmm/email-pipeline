"""Configured RFC3339 program-time helpers."""
from __future__ import annotations
import datetime as dt
import os
from zoneinfo import ZoneInfo

ENV_NAME = "EMAIL_PIPELINE_TIMEZONE"

def configure_program_timezone(name: str) -> None:
    ZoneInfo(name)
    os.environ[ENV_NAME] = name

def timezone() -> ZoneInfo:
    return ZoneInfo(os.environ.get(ENV_NAME) or "UTC")

def now() -> dt.datetime:
    return dt.datetime.now(timezone())

def format_rfc3339(value: dt.datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone()).isoformat(timespec="seconds")

def now_rfc3339() -> str:
    return format_rfc3339(now())

def parse_rfc3339(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("RFC3339 value must include timezone")
    return parsed


def filename_timestamp(value: dt.datetime) -> str:
    """Return a filesystem-safe timestamp in the configured timezone."""
    localized = value.astimezone(timezone())
    return localized.strftime("%Y%m%dT%H%M%S%z")
