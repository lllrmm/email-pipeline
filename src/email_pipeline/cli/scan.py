#!/usr/bin/env python3
"""Scan Outlook and optionally register the results in the queue."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from email_pipeline.config import default_config_path
from email_pipeline.paths import daily_root
from email_pipeline.scanner.scan import run

DEFAULT_CONFIG = default_config_path()
DEFAULT_OUTPUT_ROOT = daily_root()


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
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--output-mails", action="store_true")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    try:
        result = run(
            args.config,
            args.output_root,
            date=args.date,
            date_from=args.date_from,
            date_to=args.date_to,
            from_time=args.from_time,
            to_time=args.to_time,
            mailboxes=args.mailbox,
            limit_per_mailbox=args.limit_per_mailbox,
            register=args.register,
            include_mails=args.output_mails,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
