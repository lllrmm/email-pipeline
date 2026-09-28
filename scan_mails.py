#!/usr/bin/env python3
"""Scan Outlook through IMAPClient and emit RFC/UID transport locators."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.imap_backend import connect_imap, response_bytes  # noqa: E402
from email_pipeline.mail_identity import normalize_rfc_message_id  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402

DEFAULT_CONFIG = SCRIPT_DIR / "daily-mail-pipeline.yaml"
DEFAULT_OUTPUT_ROOT = Path.home() / ".hermes" / "email" / "daily"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def scan_log_filename(value: str) -> str:
    timestamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    return f"scan-{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}.json"


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time must include a UTC offset")
    return parsed.astimezone(dt.timezone.utc)


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def scan_range(config: dict[str, Any], start: dt.datetime, end: dt.datetime, mailboxes: list[str] | None, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    since = (start.date() - dt.timedelta(days=1)).strftime("%d-%b-%Y")
    before = (end.date() + dt.timedelta(days=1)).strftime("%d-%b-%Y")
    mails: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with connect_imap(config) as client:
        folders = mailboxes or [str(item[2]) for item in client.list_folders()]
        for folder in folders:
            try:
                selected = client.select_folder(folder, readonly=True)
                uidvalidity = int(selected[b"UIDVALIDITY"])
                uids = list(client.search(["SINCE", since, "BEFORE", before]))
                if not uids:
                    continue
                fetched = client.fetch(uids, [b"INTERNALDATE", b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
                folder_mails: list[dict[str, Any]] = []
                for uid in uids:
                    header = BytesParser(policy=policy.default).parsebytes(response_bytes(fetched[int(uid)]), headersonly=True)
                    rfc_message_id = normalize_rfc_message_id(str(header.get("Message-ID") or ""))
                    if not rfc_message_id:
                        raise RuntimeError(f"RFC Message-ID missing: {folder}/{uid}")
                    internal = fetched[int(uid)].get(b"INTERNALDATE")
                    if internal is None:
                        continue
                    if internal.tzinfo is None:
                        internal = internal.replace(tzinfo=dt.timezone.utc)
                    received_utc = internal.astimezone(dt.timezone.utc)
                    if not (start <= received_utc < end):
                        continue
                    folder_mails.append({"rfc_message_id": rfc_message_id, "folder": folder, "uidvalidity": uidvalidity, "uid": int(uid), "received_at": utc_text(received_utc)})
                mails.extend(folder_mails[-limit:])
            except Exception as exc:
                failures.append({"folder": folder, "error": str(exc)[:500]})
    return mails, failures, len(folders)


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-time", required=True)
    parser.add_argument("--to-time", required=True)
    parser.add_argument("--mailbox", action="append")
    parser.add_argument("--limit-per-mailbox", type=int, default=200)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    config = yaml.safe_load(args.config.expanduser().resolve().read_text(encoding="utf-8")) or {}
    start = parse_utc(args.from_time)
    end = parse_utc(args.to_time)
    if start >= end:
        raise RuntimeError("--from-time must be earlier than --to-time")
    first = start.date()
    last = (end - dt.timedelta(microseconds=1)).date()
    days = [(first + dt.timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    started = utc_now()
    mails, failures, mailbox_count = scan_range(config, start, end, args.mailbox, args.limit_per_mailbox)
    per_day = []
    total = 0
    for day in days:
        day_mails = [mail for mail in mails if str(mail["received_at"])[:10] == day]
        generated = utc_now()
        log = {"schema_version": 3, "artifact_type": "mail_scan_log", "status": "completed", "date": day, "from_time": utc_text(start), "to_time": utc_text(end), "started_at": started, "completed_at": generated, "generated_at": generated, "messages_total": len(day_mails), "mails": day_mails, "rfc_message_ids": [item["rfc_message_id"] for item in day_mails], "mailboxes_total": mailbox_count, "mailboxes_failed": failures}
        path = args.output_root.expanduser().resolve() / day / "scan-log" / scan_log_filename(generated)
        secure_write_text(path, json.dumps(log, ensure_ascii=False, indent=2))
        per_day.append({"date": day, "scan_log_path": str(path), "messages_total": len(day_mails), "mails": day_mails, "rfc_message_ids": log["rfc_message_ids"]})
        total += len(day_mails)
    if len(per_day) == 1:
        print(json.dumps({"ok": True, **per_day[0]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({"ok": True, "mode": "range", "messages_total": total, "per_day": per_day}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
