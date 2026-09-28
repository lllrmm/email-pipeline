#!/usr/bin/env python3
"""Lookup pipeline email identities by pipeline ID or RFC Message-ID."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path.home() / ".hermes" / "email" / "mail-index.sqlite3")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pipeline-id")
    group.add_argument("--rfc-message-id")
    args = parser.parse_args()
    index = MailIdentityIndex(args.database.expanduser().resolve())
    if args.pipeline_id:
        value = index.lookup_pipeline_id(args.pipeline_id)
    else:
        value = index.lookup_rfc_message_id(args.rfc_message_id)
    print(json.dumps(value, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
