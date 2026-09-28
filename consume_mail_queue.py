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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.config import load_config  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.mail_identity import get_or_create_salt  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402
from index_mail import register_mail  # noqa: E402
from email_pipeline.program_time import configure_program_timezone  # noqa: E402

STOP = False
CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.toml"


def stop(*_args) -> None:
    global STOP
    STOP = True


def event_day(event: dict, timezone_name: str) -> str:
    try:
        value = dt.datetime.fromisoformat(str(event.get("received_at") or "").replace("Z", "+00:00"))
        return value.astimezone(ZoneInfo(timezone_name)).date().isoformat()
    except Exception:
        return dt.datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def process_event(event: dict, config: dict, config_path: Path, index: MailIdentityIndex, timezone_name: str) -> str:
    identity_cfg = config.get("identity") or {}
    salt_path = Path(identity_cfg.get("salt_path") or (Path.home() / ".hermes/email/pipeline-id-salt")).expanduser().resolve()
    result = register_mail(event["rfc_message_id"], event["folder"], int(event["uidvalidity"]), int(event["imap_uid"]), account=str(identity_cfg.get("account") or "outlook"), salt=get_or_create_salt(salt_path), database_path=index.path)
    pipeline_id = result["pipeline_id"]
    day = event_day(event, timezone_name)
    proposed_dir = Path.home() / ".hermes/email/daily" / day / "emails" / pipeline_id
    workspace = index.ensure_workspace(pipeline_id, day, proposed_dir)
    day = workspace["date"]
    mail_dir = Path(workspace["path"])
    mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_write_text(mail_dir / "request.json", json.dumps({"pipeline_id": pipeline_id, "rfc_message_id": event["rfc_message_id"], "identity_source": "rfc_message_id", "index_database": str(index.path)}, ensure_ascii=False, indent=2))
    identity = index.lookup_pipeline_id(pipeline_id)
    if identity and identity.get("summarized") is True and (mail_dir / "summary.json").is_file():
        return pipeline_id
    completed = subprocess.run([sys.executable, str(SCRIPT_DIR / "summarize-mail-agentic.py"), "--pipeline-id", pipeline_id, "--mail-dir", str(mail_dir), "--output", str(mail_dir / "summary.json"), "--config", str(config_path)], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
    return pipeline_id


def process_claimed_event(event: dict, config: dict, config_path: Path, index: MailIdentityIndex, timezone_name: str) -> tuple[int, str | None, Exception | None]:
    queue_id = int(event["queue_id"])
    try:
        return queue_id, process_event(event, config, config_path, index, timezone_name), None
    except Exception as exc:
        return queue_id, None, exc


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = load_config(config_path)
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    timezone_name = str(config.get("timezone") or "UTC")
    concurrency = max(1, int((config.get("summarizer") or {}).get("concurrency") or 1))
    configure_program_timezone(timezone_name)
    index = MailIdentityIndex(database)
    while not STOP:
        events = index.claim_events(limit=concurrency)
        if not events:
            time.sleep(2)
            continue
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="mail-summary") as executor:
            futures = [executor.submit(process_claimed_event, event, config, config_path, index, timezone_name) for event in events]
            for future in as_completed(futures):
                queue_id, pipeline_id, error = future.result()
                if error is None and pipeline_id is not None:
                    index.complete_events([queue_id], {queue_id: pipeline_id})
                    print(json.dumps({"event": "queue_item_done", "queue_id": queue_id, "pipeline_id": pipeline_id}), flush=True)
                else:
                    index.retry_events([queue_id], str(error))
                    print(json.dumps({"event": "queue_item_error", "queue_id": queue_id, "error": str(error)[:500]}), file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
