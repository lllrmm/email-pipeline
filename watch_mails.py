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

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.imap_backend import connect_imap  # noqa: E402
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


def changed(previous: dict, current: dict) -> bool:
    return any(previous.get(folder) != value for folder, value in current.items())


def trigger_pipeline(config_path: Path, timezone_name: str) -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with LOCK_PATH.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        day = dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()
        completed = subprocess.run([
            sys.executable, str(SCRIPT_DIR / "daily-mail-pipeline.py"),
            "--date", day, "--no-aggregation", "--config", str(config_path),
        ], text=True, capture_output=True, check=False)
        stream = sys.stdout if completed.returncode == 0 else sys.stderr
        print(completed.stdout or completed.stderr, file=stream, flush=True)


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
                if previous and changed(previous, current):
                    time.sleep(debounce_seconds)
                    trigger_pipeline(config_path, timezone_name)
                previous = current
                secure_write_text(STATE_PATH, json.dumps(previous, ensure_ascii=False, indent=2))
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
