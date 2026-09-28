#!/usr/bin/env python3
"""Wake on IMAP changes and run incremental summarize-only processing."""

from __future__ import annotations

import datetime as dt
import json
import os
import signal
import sys
import time
from pathlib import Path
from email import policy
from email.parser import BytesParser

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.config import load_config  # noqa: E402
from email_pipeline.imap_backend import connect_imap, response_bytes  # noqa: E402
from email_pipeline.daily_logging import configure_daily_logger  # noqa: E402
from email_pipeline.program_time import configure_program_timezone, format_rfc3339, timezone  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex, normalize_rfc_message_id  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402

CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.toml"
STATE_PATH = Path.home() / ".hermes" / "email" / "watch-state.json"
STOP = False
LOGGER = __import__("logging").getLogger("email_pipeline.watcher")


def stop(*_args) -> None:
    global STOP
    STOP = True


def folder_snapshot(client, folders: list[str]) -> dict[str, dict[str, int]]:
    result = {}
    for folder in folders:
        status = client.folder_status(folder, [b"UIDVALIDITY", b"UIDNEXT", b"MESSAGES"])
        result[folder] = {key.decode().lower(): int(value) for key, value in status.items()}
    return result


def new_uid_ranges(previous: dict, current: dict) -> dict[str, tuple[int, int, int]]:
    result = {}
    for folder, value in current.items():
        old = previous.get(folder)
        if not old or old.get("uidvalidity") != value.get("uidvalidity"):
            continue
        start = int(old.get("uidnext", 1))
        end = int(value.get("uidnext", 1)) - 1
        if end >= start:
            result[folder] = (int(value["uidvalidity"]), start, end)
    return result


def event_mails(client, changes: dict[str, tuple[int, int, int]]) -> list[dict]:
    mails = []
    for folder, (uidvalidity, start, end) in changes.items():
        client.select_folder(folder, readonly=True)
        uids = list(client.search(["UID", f"{start}:{end}"]))
        if not uids:
            continue
        fetched = client.fetch(uids, [b"INTERNALDATE", b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
        for uid in uids:
            header = BytesParser(policy=policy.default).parsebytes(response_bytes(fetched[int(uid)]), headersonly=True)
            rfc = normalize_rfc_message_id(str(header.get("Message-ID") or ""))
            value = fetched[int(uid)].get(b"INTERNALDATE")
            if value is not None and value.tzinfo is None:
                value = value.replace(tzinfo=timezone())
            received_at = format_rfc3339(value) if value is not None else None
            mails.append({"rfc_message_id": rfc, "folder": folder, "uidvalidity": uidvalidity, "uid": int(uid), "received_at": received_at})
    return mails


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = load_config(config_path)
    boundary_timezone = str((config.get("program") or {}).get("timezone") or "UTC")
    configure_program_timezone(boundary_timezone)
    global LOGGER
    LOGGER = configure_daily_logger(Path.home() / ".hermes" / "email" / "daily", "watcher", boundary_timezone)
    watch = config.get("watcher") or {}
    idle_mailbox = str(watch.get("idle_accelerator_mailbox") or "Inbox")
    poll_seconds = max(15, int(watch.get("poll_seconds") or 60))
    debounce_seconds = max(1, int(watch.get("debounce_seconds") or 10))
    identity = config.get("identity") or {}
    account = str(identity.get("account") or "outlook")
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    index = MailIdentityIndex(database)
    previous = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
    LOGGER.info("watcher_start poll_seconds=%d debounce_seconds=%d idle_accelerator=%s", poll_seconds, debounce_seconds, idle_mailbox)
    while not STOP:
        try:
            with connect_imap(config) as client:
                folders = [str(item[2]) for item in client.list_folders()]
                current = folder_snapshot(client, folders)
                changes = new_uid_ranges(previous, current) if previous else {}
                if changes:
                    time.sleep(debounce_seconds)
                    mails = event_mails(client, changes)
                    inserted = 0
                    for mail in mails:
                        inserted += int(index.enqueue_event(account=account, rfc_message_id=mail["rfc_message_id"], folder=mail["folder"], uidvalidity=mail["uidvalidity"], uid=mail["uid"], received_at=mail.get("received_at")))
                    if mails:
                        LOGGER.info("enqueued detected=%d inserted=%d changed_folders=%s", len(mails), inserted, sorted(changes))
                previous = current
                secure_write_text(STATE_PATH, json.dumps(current, ensure_ascii=False, indent=2))
                client.select_folder(idle_mailbox, readonly=True)
                client.idle()
                client.idle_check(timeout=poll_seconds)
                client.idle_done()
        except Exception as exc:
            LOGGER.exception("watch_error")
            time.sleep(15)
    LOGGER.info("watcher_stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
