#!/usr/bin/env python3
"""Wake on IMAP changes and run incremental summarize-only processing."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from email import policy
from email.parser import BytesParser

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.imap_backend import connect_imap, response_bytes  # noqa: E402
from email_pipeline.mail_identity import normalize_rfc_message_id  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402

CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"
STATE_PATH = Path.home() / ".hermes" / "email" / "watch-state.json"
LOCK_PATH = Path.home() / ".hermes" / "email" / "watch-pipeline.lock"
STOP = False


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


def event_scan_log(config: dict, client, changes: dict[str, tuple[int, int, int]], timezone_name: str) -> Path | None:
    mails = []
    for folder, (uidvalidity, start, end) in changes.items():
        client.select_folder(folder, readonly=True)
        uids = list(client.search(["UID", f"{start}:{end}"]))
        if not uids:
            continue
        fetched = client.fetch(uids, [b"BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)]"])
        for uid in uids:
            header = BytesParser(policy=policy.default).parsebytes(response_bytes(fetched[int(uid)]), headersonly=True)
            rfc = normalize_rfc_message_id(str(header.get("Message-ID") or ""))
            mails.append({"rfc_message_id": rfc, "folder": folder, "uidvalidity": uidvalidity, "uid": int(uid)})
    if not mails:
        return None
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    day = dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    name = dt.datetime.fromisoformat(now.replace("Z", "+00:00")).strftime("scan-%Y%m%dT%H%M%S.%fZ.json")
    path = Path.home() / ".hermes/email/daily" / day / "scan-log" / name
    value = {"schema_version": 2, "artifact_type": "mail_scan_log", "status": "completed", "source": "imap_event", "date": day, "generated_at": now, "messages_total": len(mails), "mails": mails, "rfc_message_ids": [m["rfc_message_id"] for m in mails], "mailboxes_total": len(changes), "mailboxes_failed": []}
    secure_write_text(path, json.dumps(value, ensure_ascii=False, indent=2))
    return path


def trigger_pipeline(config_path: Path, scan_log_path: Path) -> bool:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        completed = subprocess.run([
            sys.executable, str(SCRIPT_DIR / "daily-mail-pipeline.py"),
            "--scan-log", str(scan_log_path), "--no-aggregation", "--config", str(config_path),
        ], text=True, capture_output=True, check=False)
        stream = sys.stdout if completed.returncode == 0 else sys.stderr
        print(completed.stdout or completed.stderr, file=stream, flush=True)
        return completed.returncode == 0


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    watch = config.get("watch") or {}
    idle_mailbox = str(watch.get("idle_accelerator_mailbox") or "Inbox")
    poll_seconds = max(15, int(watch.get("poll_seconds") or 60))
    debounce_seconds = max(1, int(watch.get("debounce_seconds") or 10))
    timezone_name = str((config.get("scan") or {}).get("timezone") or "Asia/Hong_Kong")
    previous = json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.is_file() else {}
    while not STOP:
        try:
            with connect_imap(config) as client:
                folders = [str(item[2]) for item in client.list_folders()]
                current = folder_snapshot(client, folders)
                changes = new_uid_ranges(previous, current) if previous else {}
                if changes:
                    time.sleep(debounce_seconds)
                    scan_log = event_scan_log(config, client, changes, timezone_name)
                    if scan_log is not None and not trigger_pipeline(config_path, scan_log):
                        raise RuntimeError("event pipeline failed")
                previous = current
                secure_write_text(STATE_PATH, json.dumps(current, ensure_ascii=False, indent=2))
                client.select_folder(idle_mailbox, readonly=True)
                client.idle()
                client.idle_check(timeout=poll_seconds)
                client.idle_done()
        except Exception as exc:
            print(json.dumps({"event": "watch_error", "error": str(exc)[:500]}), file=sys.stderr, flush=True)
            time.sleep(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
