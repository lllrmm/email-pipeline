#!/usr/bin/env python3
"""Consume durable new-mail queue entries and summarize without aggregation."""

from __future__ import annotations

import datetime as dt
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

from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402

STOP = False
CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"


def stop(*_args) -> None:
    global STOP
    STOP = True


def write_scan_log(events: list[dict], timezone_name: str) -> Path:
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    day = dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()
    name = dt.datetime.fromisoformat(now.replace("Z", "+00:00")).strftime("scan-%Y%m%dT%H%M%S.%fZ.json")
    mails = [{"rfc_message_id": e["rfc_message_id"], "folder": e["folder"], "uidvalidity": e["uidvalidity"], "uid": e["imap_uid"]} for e in events]
    path = Path.home() / ".hermes/email/daily" / day / "scan-log" / name
    secure_write_text(path, json.dumps({"schema_version": 2, "artifact_type": "mail_scan_log", "status": "completed", "source": "imap_event_queue", "date": day, "generated_at": now, "messages_total": len(mails), "mails": mails, "rfc_message_ids": [m["rfc_message_id"] for m in mails], "mailboxes_total": len({m["folder"] for m in mails}), "mailboxes_failed": []}, ensure_ascii=False, indent=2))
    return path


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    timezone_name = str((config.get("scan") or {}).get("timezone") or "Asia/Hong_Kong")
    index = MailIdentityIndex(database)
    while not STOP:
        events = index.claim_events(limit=20)
        if not events:
            time.sleep(2)
            continue
        ids = [int(event["queue_id"]) for event in events]
        try:
            scan_log = write_scan_log(events, timezone_name)
            completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "daily-mail-pipeline.py"), "--scan-log", str(scan_log), "--no-aggregation", "--config", str(config_path)], text=True, capture_output=True, check=False)
            if completed.returncode != 0:
                raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
            pipeline_ids = {}
            for event in events:
                matches = index.lookup_rfc_message_id(event["rfc_message_id"])
                if matches:
                    pipeline_ids[int(event["queue_id"])] = matches[0]["pipeline_id"]
            index.complete_events(ids, pipeline_ids)
            print(json.dumps({"event": "queue_batch_done", "count": len(events)}), flush=True)
        except Exception as exc:
            index.retry_events(ids, str(exc))
            print(json.dumps({"event": "queue_batch_error", "count": len(events), "error": str(exc)[:500]}), file=sys.stderr, flush=True)
            time.sleep(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
