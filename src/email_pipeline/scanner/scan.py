#!/usr/bin/env python3
"""Scan Outlook through IMAPClient and optionally register emails in the queue."""

from __future__ import annotations

import datetime as dt
import json
import logging
from email import policy
from email.parser import BytesParser
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from email_pipeline.config import load_config  # noqa: E402
from email_pipeline.daily_logging import configure_run_logger, parse_log_level  # noqa: E402
from email_pipeline.imap_backend import connect_imap, response_bytes  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex, normalize_rfc_message_id  # noqa: E402
from email_pipeline.paths import database_path as default_database_path  # noqa: E402
from email_pipeline.process_lock import ProcessLock  # noqa: E402
from email_pipeline.program_time import configure_program_timezone, format_rfc3339, timezone  # noqa: E402

LOGGER = logging.getLogger("email_pipeline.scanner")


def configure_scanner_logger(output_root: Path, timezone_name: str = "UTC", file_level: int = logging.INFO) -> None:
    global LOGGER
    LOGGER = configure_run_logger(output_root, "scanner", timezone_name, file_level, logging.ERROR)


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("time must include a timezone offset")
    return parsed.astimezone(dt.timezone.utc)


def utc_text(value: dt.datetime) -> str:
    return format_rfc3339(value)


def resolve_window(
    boundary: ZoneInfo,
    *,
    date: str | None,
    date_from: str | None,
    date_to: str | None,
    from_time: str | None,
    to_time: str | None,
) -> tuple[dt.datetime, dt.datetime]:
    if from_time or to_time:
        if not from_time or not to_time:
            raise RuntimeError("--from-time and --to-time must be provided together")
        start = parse_utc(from_time)
        end = parse_utc(to_time)
        if start >= end:
            raise RuntimeError("--from-time must be earlier than --to-time")
        return start, end
    first = dt.date.fromisoformat(date_from or date or dt.datetime.now(boundary).date().isoformat())
    last = dt.date.fromisoformat(date_to or date or first.isoformat())
    if last < first:
        raise RuntimeError("--to must not be earlier than --from")
    return (
        dt.datetime.combine(first, dt.time.min, tzinfo=boundary),
        dt.datetime.combine(last + dt.timedelta(days=1), dt.time.min, tzinfo=boundary),
    )


def scan_range(config: dict[str, Any], start: dt.datetime, end: dt.datetime, mailboxes: list[str] | None, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    since = (start.date() - dt.timedelta(days=1)).strftime("%d-%b-%Y")
    before = (end.date() + dt.timedelta(days=1)).strftime("%d-%b-%Y")
    mails: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with connect_imap(config) as client:
        folders = mailboxes or [str(item[2]) for item in client.list_folders()]
        for folder in folders:
            try:
                LOGGER.info("scan_folder_start folder=%s", json.dumps(folder, ensure_ascii=False))
                selected = client.select_folder(folder, readonly=True)
                uidvalidity = int(selected[b"UIDVALIDITY"])
                uids = list(client.search(["SINCE", since, "BEFORE", before]))
                folder_mails: list[dict[str, Any]] = []
                if uids:
                    fetched = client.fetch(uids, [b"INTERNALDATE", b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
                    for uid in uids:
                        header = BytesParser(policy=policy.default).parsebytes(response_bytes(fetched[int(uid)]), headersonly=True)
                        rfc_message_id = normalize_rfc_message_id(str(header.get("Message-ID") or ""))
                        if not rfc_message_id:
                            raise RuntimeError(f"RFC Message-ID missing: {folder}/{uid}")
                        internal = fetched[int(uid)].get(b"INTERNALDATE")
                        if internal is None:
                            continue
                        if internal.tzinfo is None:
                            internal = internal.replace(tzinfo=timezone())
                        received_instant = internal.astimezone(dt.timezone.utc)
                        if not (start <= received_instant < end):
                            continue
                        folder_mails.append({"rfc_message_id": rfc_message_id, "folder": folder, "uidvalidity": uidvalidity, "uid": int(uid), "received_at": utc_text(received_instant)})
                    mails.extend(folder_mails[-limit:])
                LOGGER.info("scan_folder_done folder=%s candidates=%d matched=%d", json.dumps(folder, ensure_ascii=False), len(uids), len(folder_mails[-limit:]))
            except Exception as exc:
                failures.append({"folder": folder, "error": str(exc)[:500]})
                LOGGER.exception("scan_folder_failed folder=%s", json.dumps(folder, ensure_ascii=False))
    return mails, failures, len(folders)


def enqueue_scan_result(result: dict[str, Any], config_path: Path) -> dict[str, Any]:
    config = load_config(config_path)
    identity_cfg = config.get("identity") or {}
    account = str(identity_cfg.get("account") or "outlook")
    database = Path(
        identity_cfg.get("database_path") or default_database_path()
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


def run(
    config_path: Path,
    output_root: Path,
    *,
    date: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    mailboxes: list[str] | None = None,
    limit_per_mailbox: int = 200,
    register: bool = False,
    include_mails: bool = False,
) -> dict[str, Any]:
    config_path = config_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    config = load_config(config_path)
    boundary_timezone = str(config.get("timezone") or "UTC")
    configure_program_timezone(boundary_timezone)
    scanner_cfg = config.get("scanner") or {}
    configure_scanner_logger(output_root, boundary_timezone, parse_log_level(scanner_cfg.get("log_level_file")))
    boundary = ZoneInfo(boundary_timezone)
    start, end = resolve_window(boundary, date=date, date_from=date_from, date_to=date_to, from_time=from_time, to_time=to_time)
    LOGGER.info("scan_start from_time=%s to_time=%s requested_mailboxes=%s", utc_text(start), utc_text(end), json.dumps(mailboxes if mailboxes is not None else "all", ensure_ascii=False))
    first = start.astimezone(boundary).date()
    last = (end - dt.timedelta(microseconds=1)).astimezone(boundary).date()
    days = [(first + dt.timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]
    try:
        with ProcessLock("scanner"):
            mails, failures, mailbox_count = scan_range(config, start, end, mailboxes, limit_per_mailbox)
            per_day: list[dict[str, Any]] = []
            total = 0
            for day in days:
                day_mails = [mail for mail in mails if parse_utc(str(mail["received_at"])).astimezone(boundary).date().isoformat() == day]
                total += len(day_mails)
                queued = skipped = None
                if register:
                    counts = enqueue_scan_result({"date": day, "mails": day_mails, "mailboxes_total": mailbox_count, "mailboxes_failed": failures}, config_path)
                    queued, skipped = counts["queued"], counts["skipped"]
                    LOGGER.info("scan_registered date=%s queued=%d skipped=%d", day, queued, skipped)
                per_day.append({
                    "date": day,
                    "messages_total": len(day_mails),
                    "mailboxes_total": mailbox_count,
                    "mailboxes_failed": failures,
                    **({"queued": queued, "skipped": skipped} if register else {}),
                    **({"mails": day_mails} if include_mails else {}),
                })
    except Exception:
        LOGGER.exception("scan_failed")
        raise
    LOGGER.info("scan_complete messages_total=%d mailboxes_total=%d failures=%d", total, mailbox_count, len(failures))
    return {"ok": True, "mode": "range" if len(per_day) > 1 else "single", "per_day": per_day}
