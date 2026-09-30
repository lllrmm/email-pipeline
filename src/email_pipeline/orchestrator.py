#!/usr/bin/env python3
"""Run one instance: summarize queued mail and keep the Outlook token fresh."""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from email_pipeline.config import default_config_path, load_config  # noqa: E402
from email_pipeline.daily_logging import configure_daily_logger, parse_log_level  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.mail_identity import get_or_create_salt  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402
from email_pipeline.registry import register_mail  # noqa: E402
from email_pipeline.program_time import configure_program_timezone  # noqa: E402
from email_pipeline.paths import auth_db_path, daily_root, database_path as default_database_path, salt_path as default_salt_path, token_refresh_path  # noqa: E402

STOP = False
CONFIG_PATH = default_config_path()
TOKEN_REFRESH_TIMEOUT_SECONDS = 60
LOGGER = logging.getLogger("email_pipeline.orchestrator")


def configure_orchestrator_logger(timezone_name: str = "UTC", file_level: int = logging.INFO, console_level: int | None = None) -> None:
    global LOGGER
    LOGGER = configure_daily_logger(daily_root(), "orchestrator", timezone_name, file_level, console_level)


def log_event(level: int, event: str, **fields: Any) -> None:
    message = " ".join([event, *(f"{key}={value}" for key, value in fields.items())])
    LOGGER.log(level, message)


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
    salt_path = Path(identity_cfg.get("salt_path") or default_salt_path()).expanduser().resolve()
    result = register_mail(event["rfc_message_id"], event["folder"], int(event["uidvalidity"]), int(event["imap_uid"]), account=str(identity_cfg.get("account") or "outlook"), salt=get_or_create_salt(salt_path), database_path=index.path)
    pipeline_id = result["pipeline_id"]
    day = event_day(event, timezone_name)
    proposed_dir = daily_root() / day / "emails" / pipeline_id
    workspace = index.ensure_workspace(pipeline_id, day, proposed_dir)
    day = workspace["date"]
    mail_dir = Path(workspace["path"])
    mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secure_write_text(mail_dir / "request.json", json.dumps({"pipeline_id": pipeline_id, "rfc_message_id": event["rfc_message_id"], "identity_source": "rfc_message_id", "index_database": str(index.path)}, ensure_ascii=False, indent=2))
    identity = index.lookup_pipeline_id(pipeline_id)
    if identity and identity.get("summarized") is True and (mail_dir / "summary.json").is_file():
        return pipeline_id
    completed = subprocess.run([sys.executable, "-m", "email_pipeline", "summarize", "--pipeline-id", pipeline_id, "--mail-dir", str(mail_dir), "--output", str(mail_dir / "summary.json"), "--config", str(config_path)], text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout)[-1000:])
    return pipeline_id


def process_claimed_event(event: dict, config: dict, config_path: Path, index: MailIdentityIndex, timezone_name: str) -> tuple[int, str | None, Exception | None]:
    queue_id = int(event["queue_id"])
    try:
        return queue_id, process_event(event, config, config_path, index, timezone_name), None
    except Exception as exc:
        return queue_id, None, exc


def token_expires_at() -> int | None:
    try:
        connection = sqlite3.connect(f"file:{auth_db_path()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        row = connection.execute("SELECT expires_at FROM tokens WHERE provider=?", ("outlook",)).fetchone()
        return int(row[0]) if row else None
    except sqlite3.Error:
        return None
    finally:
        connection.close()


def refresh_token(window_seconds: int) -> None:
    before = token_expires_at()
    if before is not None and before - int(time.time()) > window_seconds:
        log_event(logging.DEBUG, "token_check_ok")
        return
    log_event(logging.INFO, "token_refresh")
    try:
        completed = subprocess.run([str(token_refresh_path())], text=True, capture_output=True, timeout=TOKEN_REFRESH_TIMEOUT_SECONDS, check=False)
    except Exception as exc:
        log_event(logging.ERROR, "token_refresh_error", error=str(exc)[:500])
        return
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[-500:] or f"exit status {completed.returncode}"
        log_event(logging.ERROR, "token_refresh_error", error=detail)
        return
    after = token_expires_at()
    if before is not None and after is not None and after <= before:
        log_event(logging.DEBUG, "token_check_ok")
        return
    log_event(logging.INFO, "token_refresh_ok")


def main() -> int:
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    configure_orchestrator_logger()
    config_path = Path(os.environ.get("EMAIL_PIPELINE_CONFIG") or CONFIG_PATH).expanduser().resolve()
    config = load_config(config_path)
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or default_database_path()).expanduser().resolve()
    timezone_name = str(config.get("timezone") or "UTC")
    orchestrator_cfg = config.get("orchestrator") or {}
    concurrency = max(1, int(orchestrator_cfg.get("summarizer_concurrency") or 1))
    token_refresh_check_seconds = max(1, int(orchestrator_cfg.get("token_refresh_check_seconds") or 60))
    token_refresh_window_seconds = max(1, int(orchestrator_cfg.get("token_refresh_window_seconds") or 300))
    file_level = parse_log_level(orchestrator_cfg.get("log_level_file"))
    console_level = parse_log_level(orchestrator_cfg.get("log_level_console"), default=file_level)
    configure_program_timezone(timezone_name)
    configure_orchestrator_logger(timezone_name, file_level, console_level)
    index = MailIdentityIndex(database)
    log_event(logging.INFO, "orchestrator_start", summarizer_concurrency=concurrency, token_refresh_check_seconds=token_refresh_check_seconds, token_refresh_window_seconds=token_refresh_window_seconds)
    last_token_check = 0.0
    while not STOP:
        if time.monotonic() - last_token_check >= token_refresh_check_seconds:
            last_token_check = time.monotonic()
            refresh_token(token_refresh_window_seconds)
        events = index.claim_events(limit=concurrency)
        if not events:
            time.sleep(2)
            continue
        log_event(logging.INFO, "queue_claimed", count=len(events))
        attempts_by_queue = {int(event["queue_id"]): int(event.get("attempts") or 0) for event in events}
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="summarizer") as executor:
            futures = [executor.submit(process_claimed_event, event, config, config_path, index, timezone_name) for event in events]
            for future in as_completed(futures):
                queue_id, pipeline_id, error = future.result()
                if error is None and pipeline_id is not None:
                    index.complete_events([queue_id], {queue_id: pipeline_id})
                    log_event(logging.INFO, "queue_item_done", queue_id=queue_id, pipeline_id=pipeline_id)
                else:
                    index.retry_events([queue_id], str(error))
                    log_event(logging.WARNING, "queue_item_retry", queue_id=queue_id, attempts=attempts_by_queue.get(queue_id, 0))
                    log_event(logging.ERROR, "queue_item_error", queue_id=queue_id, error=str(error)[:500])
    log_event(logging.INFO, "orchestrator_stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
