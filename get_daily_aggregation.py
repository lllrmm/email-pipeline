#!/usr/bin/env python3
"""Wait for one day's mail queue to finish, then create an aggregation."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.config import load_config  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.program_time import configure_program_timezone  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "daily-mail-pipeline.toml"
DEFAULT_OUTPUT_ROOT = Path.home() / ".hermes/email/daily"


def parse_timestamp(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def event_date(event: dict[str, Any], index: MailIdentityIndex, timezone: ZoneInfo) -> str | None:
    timestamp = parse_timestamp(event.get("received_at"))
    if timestamp is None:
        timestamp = parse_timestamp(event.get("detected_at"))
    return timestamp.astimezone(timezone).date().isoformat() if timestamp else None


def events_for_date(index: MailIdentityIndex, target_date: str, timezone: ZoneInfo) -> list[dict[str, Any]]:
    return [event for event in index.list_queue_events() if event_date(event, index, timezone) == target_date]


def wait_for_done(
    index: MailIdentityIndex,
    target_date: str,
    timezone: ZoneInfo,
    *,
    timeout: int,
    poll_seconds: float,
    settle_seconds: float,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    stable_signature: tuple[tuple[int, str], ...] | None = None
    stable_since = 0.0
    while True:
        events = events_for_date(index, target_date, timezone)
        pending = [event for event in events if event.get("status") != "done"]
        signature = tuple((int(event["queue_id"]), str(event.get("status"))) for event in events)
        now = time.monotonic()
        if not pending:
            if signature == stable_signature:
                if now - stable_since >= settle_seconds:
                    return events
            else:
                stable_signature = signature
                stable_since = now
        else:
            stable_signature = None
            stable_since = 0.0
        if now >= deadline:
            detail = [
                {
                    "queue_id": event.get("queue_id"),
                    "status": event.get("status"),
                    "attempts": event.get("attempts"),
                    "last_error": event.get("last_error"),
                }
                for event in pending
            ]
            raise RuntimeError(f"timed out waiting for queue: {json.dumps(detail, ensure_ascii=False)}")
        time.sleep(poll_seconds)


def run_aggregation(config_path: Path, output_root: Path, target_date: str, pipeline_ids: list[str]) -> dict[str, Any]:
    day_dir = output_root / target_date
    command = [
        sys.executable,
        str(SCRIPT_DIR / "aggregate-mails-agentic.py"),
        "--pipeline-id-list",
        *pipeline_ids,
        "--agent-workdir",
        str(day_dir),
        "--output-dir",
        str(day_dir / "aggregation"),
        "--config",
        str(config_path),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
    result = json.loads(completed.stdout)
    if not result.get("ok"):
        raise RuntimeError(str(result))
    return result


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Wait for a day's queue and aggregate its mail summaries.")
    parser.add_argument("--date")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--wait-timeout", type=int, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    args = parser.parse_args()

    config_path = args.config.expanduser().resolve()
    config = load_config(config_path)
    timezone = ZoneInfo(str(config.get("timezone") or "UTC"))
    configure_program_timezone(str(timezone))
    target_date = args.date or dt.datetime.now(timezone).date().isoformat()
    dt.date.fromisoformat(target_date)
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    index = MailIdentityIndex(database)
    try:
        events = wait_for_done(
            index,
            target_date,
            timezone,
            timeout=max(1, args.wait_timeout),
            poll_seconds=max(0.2, args.poll_seconds),
            settle_seconds=max(0.0, args.settle_seconds),
        )
        missing = [event["queue_id"] for event in events if not event.get("pipeline_id")]
        if missing:
            raise RuntimeError(f"done queue items missing pipeline_id: {missing}")
        pipeline_ids = list(dict.fromkeys(str(event["pipeline_id"]) for event in events))
        result = run_aggregation(args.config.expanduser().resolve(), args.output_root.expanduser().resolve(), target_date, pipeline_ids)
        print(json.dumps({
            "ok": True,
            "date": target_date,
            "queue_events": len(events),
            "pipeline_ids": len(pipeline_ids),
            "aggregation_path": result.get("summary_path"),
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "date": target_date, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
