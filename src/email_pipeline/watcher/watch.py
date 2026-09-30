#!/usr/bin/env python3
"""Poll every IMAP folder for new UIDs and enqueue new messages."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from email import policy
from email.parser import BytesParser

from email_pipeline.config import default_config_path, load_config  # noqa: E402
from email_pipeline.imap_backend import connect_imap, response_bytes  # noqa: E402
from email_pipeline.daily_logging import configure_daily_logger, parse_log_level  # noqa: E402
from email_pipeline.program_time import configure_program_timezone, format_rfc3339, timezone  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex, normalize_rfc_message_id  # noqa: E402
from email_pipeline.paths import daily_root, database_path as default_database_path  # noqa: E402
from email_pipeline.process_lock import ProcessLock  # noqa: E402

CONFIG_PATH = default_config_path()
STOP = False
LOGGER = __import__("logging").getLogger("email_pipeline.watcher")


def configure_watcher_logger(timezone_name: str = "UTC", file_level: int = logging.INFO, console_level: int | None = None) -> None:
    global LOGGER
    LOGGER = configure_daily_logger(daily_root(), "watcher", timezone_name, file_level, console_level)


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
    configure_watcher_logger()
    try:
        with ProcessLock("watcher"):
            return _run_watcher()
    except Exception:
        LOGGER.exception("watcher_failed")
        return 1


def _run_watcher() -> int:
    try:
        config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
        config = load_config(config_path)
        boundary_timezone = str(config.get("timezone") or "UTC")
        configure_program_timezone(boundary_timezone)
        watch = config.get("watcher") or {}
        file_level = parse_log_level(watch.get("log_level_file"))
        console_level = parse_log_level(watch.get("log_level_console"), default=file_level)
        configure_watcher_logger(boundary_timezone, file_level, console_level)
        poll_seconds = max(15, int(watch.get("poll_seconds") or 60))
        debounce_seconds = max(1, int(watch.get("debounce_seconds") or 10))
        database = default_database_path()
        index = MailIdentityIndex(database)
        previous = index.load_watch_snapshot()
        LOGGER.info("watcher_start mode=all_folder_uidnext_poll poll_seconds=%d debounce_seconds=%d", poll_seconds, debounce_seconds)
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
                            inserted += int(index.enqueue_event(rfc_message_id=mail["rfc_message_id"], folder=mail["folder"], uidvalidity=mail["uidvalidity"], uid=mail["uid"], received_at=mail.get("received_at")))
                        if mails:
                            LOGGER.info("enqueued detected=%d inserted=%d changed_folders=%s", len(mails), inserted, json.dumps(sorted(changes), ensure_ascii=False))
                    previous = current
                    index.replace_watch_snapshot(current)
            except Exception:
                LOGGER.exception("watch_error")
                time.sleep(15)
                continue
            time.sleep(poll_seconds)
        LOGGER.info("watcher_stop")
        return 0
    except Exception:
        LOGGER.exception("watcher_failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
