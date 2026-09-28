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
from email_pipeline.mail_identity import get_or_create_salt  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402
from index_mail import register_mail  # noqa: E402
from email_pipeline.program_time import configure_program_timezone, filename_timestamp, now_rfc3339  # noqa: E402

STOP = False
CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"


def stop(*_args) -> None:
    global STOP
    STOP = True


def event_day(event: dict, timezone_name: str) -> str:
    try:
        value = dt.datetime.fromisoformat(str(event.get("received_at") or "").replace("Z", "+00:00"))
        return value.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def write_scan_log(events: list[dict], timezone_name: str) -> Path:
    now = now_rfc3339()
    day = event_day(events[0], timezone_name)
    name = f"scan-{filename_timestamp(dt.datetime.fromisoformat(now))}.json"
    mails = [{"rfc_message_id": e["rfc_message_id"], "folder": e["folder"], "uidvalidity": e["uidvalidity"], "uid": e["imap_uid"], "received_at": e.get("received_at")} for e in events]
    path = Path.home() / ".hermes/email/daily" / day / "scan-log" / name
    secure_write_text(path, json.dumps({"schema_version": 2, "artifact_type": "mail_scan_log", "status": "completed", "source": "imap_event_queue", "date": day, "generated_at": now, "messages_total": len(mails), "mails": mails, "rfc_message_ids": [m["rfc_message_id"] for m in mails], "mailboxes_total": len({m["folder"] for m in mails}), "mailboxes_failed": []}, ensure_ascii=False, indent=2))
    return path


def process_event(event: dict, config: dict, config_path: Path, index: MailIdentityIndex, timezone_name: str) -> str:
    identity_cfg = config.get("identity") or {}
    salt_path = Path(identity_cfg.get("salt_path") or (Path.home() / ".hermes/email/pipeline-id-salt")).expanduser().resolve()
    result = register_mail(event["rfc_message_id"], event["folder"], int(event["uidvalidity"]), int(event["imap_uid"]), account=str(identity_cfg.get("account") or "outlook"), salt=get_or_create_salt(salt_path), database_path=index.path)
    pipeline_id = result["pipeline_id"]
    day = event_day(event, timezone_name)
    mail_dir = Path.home() / ".hermes/email/daily" / day / "emails" / pipeline_id
    mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    index.record_workspace(pipeline_id, day, mail_dir)
    secure_write_text(mail_dir / "request.json", json.dumps({"pipeline_id": pipeline_id, "rfc_message_id": event["rfc_message_id"], "identity_source": "rfc_message_id", "index_database": str(index.path)}, ensure_ascii=False, indent=2))
    identity = index.lookup_pipeline_id(pipeline_id)
    if identity and identity.get("summarized") is True and (mail_dir / "summary.json").is_file():
        return pipeline_id
    completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "summarize-mail-agentic.py"), "--pipeline-id", pipeline_id, "--mail-dir", str(mail_dir), "--output", str(mail_dir / "summary.json"), "--config", str(config_path)], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
    return pipeline_id


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    timezone_name = str((config.get("program") or {}).get("timezone") or "UTC")
    configure_program_timezone(timezone_name)
    index = MailIdentityIndex(database)
    while not STOP:
        events = index.claim_events(limit=20)
        if not events:
            time.sleep(2)
            continue
        write_scan_log(events, timezone_name)
        for event in events:
            queue_id = int(event["queue_id"])
            try:
                pipeline_id = process_event(event, config, config_path, index, timezone_name)
                index.complete_events([queue_id], {queue_id: pipeline_id})
                print(json.dumps({"event": "queue_item_done", "queue_id": queue_id, "pipeline_id": pipeline_id}), flush=True)
            except Exception as exc:
                index.retry_events([queue_id], str(exc))
                print(json.dumps({"event": "queue_item_error", "queue_id": queue_id, "error": str(exc)[:500]}), file=sys.stderr, flush=True)
                time.sleep(15)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
