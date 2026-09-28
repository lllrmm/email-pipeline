#!/usr/bin/env python3
"""Scan a time range and enqueue emails that still require summarization."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from email_pipeline.config import default_config_path, load_config  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.program_time import configure_program_timezone  # noqa: E402

DEFAULT_CONFIG = default_config_path()
DEFAULT_OUTPUT_ROOT = Path.home() / ".hermes/email/daily"


def run_stage(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
    value = json.loads(completed.stdout)
    if not value.get("ok"):
        raise RuntimeError(str(value))
    return value


def enqueue_scan_result(result: dict[str, Any], config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    identity_cfg = config.get("identity") or {}
    account = str(identity_cfg.get("account") or "outlook")
    database = Path(
        identity_cfg.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")
    ).expanduser().resolve()
    index = MailIdentityIndex(database)
    queued = skipped = 0
    mails = result.get("mails") or []
    for mail in mails:
        inserted = index.enqueue_event(
            account=account,
            rfc_message_id=str(mail["rfc_message_id"]),
            folder=str(mail["folder"]),
            uidvalidity=int(mail["uidvalidity"]),
            uid=int(mail["uid"]),
            received_at=mail.get("received_at"),
        )
        if inserted:
            queued += 1
        else:
            skipped += 1
    return {
        "date": result.get("date"),
        "messages_total": len(mails),
        "queued": queued,
        "skipped": skipped,
        "mailboxes_total": result.get("mailboxes_total"),
        "mailboxes_failed": result.get("mailboxes_failed") or [],
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--from", dest="date_from")
    parser.add_argument("--to", dest="date_to")
    parser.add_argument("--from-time")
    parser.add_argument("--to-time")
    parser.add_argument("--mailbox", action="append")
    parser.add_argument("--limit-per-mailbox", type=int, default=200)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    config_path = args.config.expanduser().resolve()
    try:
        config = load_config(config_path)
        boundary = ZoneInfo(str(config.get("timezone") or "UTC"))
        configure_program_timezone(str(boundary))
        command = [
            sys.executable,
            "-m", "email_pipeline", "scan",
            "--config",
            str(config_path),
            "--output-root",
            str(args.output_root.expanduser()),
            "--limit-per-mailbox",
            str(args.limit_per_mailbox),
        ]
        if args.from_time or args.to_time:
            if not args.from_time or not args.to_time:
                raise RuntimeError("--from-time and --to-time must be provided together")
            from_time, to_time = args.from_time, args.to_time
        else:
            first = dt.date.fromisoformat(args.date_from or args.date or dt.datetime.now(boundary).date().isoformat())
            last = dt.date.fromisoformat(args.date_to or args.date or first.isoformat())
            from_time = dt.datetime.combine(first, dt.time.min, tzinfo=boundary).isoformat()
            to_time = dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=boundary).isoformat()
        command.extend(["--from-time", from_time, "--to-time", to_time])
        for mailbox in args.mailbox or []:
            command.extend(["--mailbox", mailbox])
        result = run_stage(command)
        scan_results = result.get("per_day") or [result]
        per_day = [enqueue_scan_result(item, config_path) for item in scan_results]
        print(json.dumps({
            "ok": True,
            "mode": "range" if len(per_day) > 1 else "single",
            "per_day": per_day,
        }, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
