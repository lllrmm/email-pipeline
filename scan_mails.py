#!/usr/bin/env python3
"""Read-only Outlook envelope scanner and stable workspace indexer.

This stage reads envelope metadata only. It never reads message bodies,
downloads attachments, unpacks MIME, or calls a model.

Usage:
    python3 scan_mails.py                                    # today
    python3 scan_mails.py --date 2026-09-25                  # one day
    python3 scan_mails.py --from 2026-09-01 --to 2026-09-27  # range backfill
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

HOME = Path.home()
SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mime_extract import secure_write_text  # noqa: E402
from email_pipeline.mail_identity import MailIdentityIndex, get_or_create_salt  # noqa: E402
from index_mail import register_rfc_message_id  # noqa: E402

# Ensure user-local binaries (himalaya lives in ~/.local/bin) are reachable
# regardless of how the script is invoked (cron, non-interactive SSH, etc.).
_LOCAL_BIN = str(HOME / ".local" / "bin")
if _LOCAL_BIN not in os.environ.get("PATH", "").split(os.pathsep):
    os.environ["PATH"] = _LOCAL_BIN + os.pathsep + os.environ.get("PATH", "")

OUT_ROOT = HOME / ".hermes" / "email" / "daily"
HIMALAYA_TIMEOUT = 90
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def scan_log_filename(generated_at: str) -> str:
    timestamp = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    timestamp = timestamp.astimezone(dt.timezone.utc)
    return f"scan-{timestamp.strftime('%Y%m%dT%H%M%S.%fZ')}.json"

def run(cmd: list[str], timeout: int = HIMALAYA_TIMEOUT, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        input=input_text,
        text=True,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )


def run_bytes(cmd: list[str], timeout: int = HIMALAYA_TIMEOUT) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        env={**os.environ, "NO_COLOR": "1"},
    )


def retry_waits(cfg: dict[str, Any]) -> list[int]:
    section = cfg.get("himalaya_retry") or {}
    raw = section.get("waits")
    if raw is None:
        raw = [20, 60]
    try:
        return [max(0, int(w)) for w in raw]
    except Exception:
        return [20, 60]


def throttle_delays(cfg: dict[str, Any]) -> tuple[float, float]:
    """(scan_delay, read_delay) in seconds between IMAP operations, to stay under Outlook's login rate limits."""
    section = cfg.get("throttle") or {}
    try:
        scan_delay = max(0.0, float(section.get("scan_delay_seconds") or 0))
        read_delay = max(0.0, float(section.get("read_delay_seconds") or 0))
    except Exception:
        return 0.0, 0.0
    return scan_delay, read_delay


def run_himalaya(cmd: list[str], cfg: dict[str, Any], label: str, timeout: int = HIMALAYA_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a himalaya command, retrying transient failures (Outlook OAuth throttling returns rc != 0)."""
    waits = retry_waits(cfg)
    last_error = "unknown error"
    for attempt in range(len(waits) + 1):
        try:
            cp = run(cmd, timeout=timeout)
        except Exception as exc:
            last_error = f"exception: {exc}"
        else:
            if cp.returncode == 0:
                return cp
            last_error = f"rc={cp.returncode}: {((cp.stderr or '') + (cp.stdout or '')).strip()[:400]}"
        if attempt < len(waits):
            print(f"[himalaya-retry] {label}: {last_error[:200]} — sleep {waits[attempt]}s", file=sys.stderr, flush=True)
            time.sleep(waits[attempt])
    raise RuntimeError(f"command failed after retries: {' '.join(cmd)}\n{last_error}")


def run_himalaya_bytes(
    cmd: list[str],
    cfg: dict[str, Any],
    label: str,
    timeout: int = HIMALAYA_TIMEOUT,
) -> subprocess.CompletedProcess[bytes]:
    waits = retry_waits(cfg)
    last_error = "unknown error"
    for attempt in range(len(waits) + 1):
        try:
            cp = run_bytes(cmd, timeout=timeout)
        except Exception as exc:
            last_error = f"exception: {exc}"
        else:
            if cp.returncode == 0:
                return cp
            output = (cp.stderr or b"") + (cp.stdout or b"")
            last_error = f"rc={cp.returncode}: {output.decode('utf-8', errors='replace').strip()[:400]}"
        if attempt < len(waits):
            print(
                f"[himalaya-retry] {label}: {last_error[:200]} - sleep {waits[attempt]}s",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(waits[attempt])
    raise RuntimeError(f"command failed after retries: {' '.join(cmd)}\n{last_error}")


def hjson(cmd: list[str], cfg: dict[str, Any], label: str, timeout: int = HIMALAYA_TIMEOUT) -> Any:
    cp = run_himalaya(cmd, cfg, label, timeout)
    return json.loads(cp.stdout or "{}")


def get_scan_date(date_arg: str | None = None) -> tuple[str, str]:
    # Use system timezone; this machine is configured for HKT in Hermes context.
    now = dt.datetime.now().astimezone()
    if date_arg:
        dt.date.fromisoformat(date_arg)  # validate
        return date_arg, f"{now.strftime('%Y-%m-%d %H:%M:%S %Z')} (generated for {date_arg})"
    return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m-%d %H:%M:%S %Z")


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")[:80] or "mailbox"


def envelope_local_date(env: dict[str, Any], tz: ZoneInfo) -> str | None:
    raw = env.get("date")
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(tz).date().isoformat()
    except Exception:
        return None


def envelope_in_window(env: dict[str, Any], tz: ZoneInfo, start_date: str, next_date: str) -> bool:
    """True when the envelope's sent-at (converted to scan timezone) falls in [start, next)."""
    day = envelope_local_date(env, tz)
    if day is None:
        return True
    return start_date <= day < next_date


def envelope_address_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        rendered = [envelope_address_text(item) for item in value]
        return ", ".join(item for item in rendered if item) or None
    if isinstance(value, dict):
        name = str(value.get("name") or "").strip()
        address = str(value.get("addr") or value.get("address") or value.get("email") or "").strip()
        if name and address:
            return f"{name} <{address}>"
        return address or name or None
    return str(value).strip() or None


def register_scanned_envelope(
    folder: str,
    envelope: dict[str, Any],
    day_dir: Path,
    *,
    account: str,
    salt: bytes,
    database_path: Path,
    observed_date: str,
) -> dict[str, Any]:
    himalaya_id = str(envelope.get("id") or "").strip()
    if not himalaya_id:
        raise RuntimeError(f"Himalaya message ID missing for envelope in {folder}")
    rfc_message_id = str(envelope.get("message-id") or "").strip()
    if not rfc_message_id:
        raise RuntimeError(f"RFC Message-ID missing for envelope {folder}/{himalaya_id}")
    registration = register_rfc_message_id(
        rfc_message_id,
        salt=salt,
        database_path=database_path,
    )
    pipeline_id = registration["pipeline_id"]
    index = MailIdentityIndex(database_path)
    index.record_location(
        pipeline_id=pipeline_id,
        account=account,
        folder=folder,
        himalaya_id=himalaya_id,
        observed_date=observed_date,
    )
    index.update_metadata(
        pipeline_id,
        sent_at=str(envelope.get("date") or "").strip() or None,
        subject=str(envelope.get("subject") or "").strip() or None,
        sender=envelope_address_text(envelope.get("from")),
    )
    mail_dir = day_dir / "emails" / pipeline_id
    mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    index.record_workspace(pipeline_id, observed_date, mail_dir)
    request = {
        "pipeline_id": pipeline_id,
        "rfc_message_id": rfc_message_id,
        "identity_source": "rfc_message_id",
        "subject": envelope.get("subject"),
        "date": envelope.get("date"),
        "index_database": str(database_path),
    }
    secure_write_text(mail_dir / "request.json", json.dumps(request, ensure_ascii=False, indent=2))
    return {
        **registration,
        "rfc_message_id": rfc_message_id,
        "observed_date": observed_date,
        "mail_dir": str(mail_dir),
    }


# ---------------------------------------------------------------------------
# Config & DeepSeek API
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"config file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise RuntimeError(f"config must be a YAML mapping: {path}")
    return cfg


def classify_summary_state(database_path: Path, day_dir: Path, pipeline_id: str) -> str:
    """Return pending or reused for one registered mail."""
    identity = MailIdentityIndex(database_path).lookup_pipeline_id(pipeline_id)
    if identity is None:
        raise RuntimeError(f"pipeline id is absent from database: {pipeline_id}")
    if identity.get("summarized") is not True:
        return "pending"
    summary_path = day_dir / "emails" / pipeline_id / "summary.json"
    if not summary_path.is_file():
        raise RuntimeError(f"database says summarized but summary.json is missing: {pipeline_id}")
    artifact = json.loads(summary_path.read_text(encoding="utf-8"))
    if artifact.get("pipeline_id") != pipeline_id or not isinstance(artifact.get("analysis"), dict):
        raise RuntimeError(f"database says summarized but summary.json is invalid: {pipeline_id}")
    return "reused"


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Scan Outlook envelope metadata and build stable email workspaces.")
    parser.add_argument("--date", help="HKT date to scan, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD", help="Range backfill start date (inclusive); pair with --to.")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD", help="Range backfill end date (inclusive); pair with --from.")
    parser.add_argument("--mailbox", action="append", help="Limit to one mailbox; can repeat. Defaults to all mailboxes.")
    parser.add_argument("--limit-per-mailbox", type=int, default=200, help="Max envelopes per mailbox.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help=f"YAML config path (default: {DEFAULT_CONFIG_PATH})")
    parser.add_argument("--output-root", default=str(OUT_ROOT), help=f"Output root (default: {OUT_ROOT})")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    out_root = Path(args.output_root).expanduser().resolve()
    started_at = utc_now()

    # ---- date window(s) ---------------------------------------------------
    try:
        if args.date_from or args.date_to:
            start_raw = args.date_from or args.date_to
            end_raw = args.date_to or args.date_from
            start_d = dt.date.fromisoformat(start_raw)
            end_d = dt.date.fromisoformat(end_raw)
            if start_d > end_d:
                raise ValueError(f"--from {start_raw} is after --to {end_raw}")
        else:
            day, _ = get_scan_date(args.date)
            start_d = end_d = dt.date.fromisoformat(day)
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad_date: {e}"}, ensure_ascii=False))
        return 1

    days = [(start_d + dt.timedelta(days=i)).isoformat() for i in range((end_d - start_d).days + 1)]
    range_mode = len(days) > 1
    first_day, last_day = days[0], days[-1]

    def next_day(d: str) -> str:
        return (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()

    # himalaya's query DSL has no `before`, and `after` is date-granular, so we
    # query from the previous day then filter precisely by the scan timezone.
    search_query = f"after {(dt.date.fromisoformat(first_day) - dt.timedelta(days=1)).isoformat()}"
    window_end = next_day(last_day)

    out_dirs = {d: out_root / d for d in days}
    for d in days:
        path = out_dirs[d] / "emails"
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.chmod(0o700)

    def write_error_logs(error: str) -> list[str]:
        paths: list[str] = []
        for day in days:
            generated_at = utc_now()
            log = {
                "schema_version": 1,
                "artifact_type": "mail_scan_log",
                "status": "failed",
                "date": day,
                "started_at": started_at,
                "completed_at": generated_at,
                "generated_at": generated_at,
                "fatal_error": error,
                "messages_total": 0,
                "rfc_message_ids": [],
                "included_pipeline_ids": [],
                "pending_summary_pipeline_ids": [],
                "reused_summary_pipeline_ids": [],
                "mailboxes_failed": [],
            }
            log_dir = out_dirs[day] / "scan-log"
            path = log_dir / scan_log_filename(generated_at)
            secure_write_text(path, json.dumps(log, ensure_ascii=False, indent=2))
            paths.append(str(path))
        return paths

    # ---- config -----------------------------------------------------------
    try:
        cfg = load_config(config_path)
    except Exception as e:
        error = f"config_error: {e}"
        paths = write_error_logs(error)
        print(json.dumps({"ok": False, "scan_log_paths": paths, "error": error}, ensure_ascii=False))
        return 1

    identity_cfg = cfg.get("identity") or {}
    salt_path = Path(identity_cfg.get("salt_path") or (HOME / ".hermes" / "email" / "pipeline-id-salt")).expanduser().resolve()
    database_path = Path(identity_cfg.get("database_path") or (HOME / ".hermes" / "email" / "mail-index.sqlite3")).expanduser().resolve()
    account_name = str(identity_cfg.get("account") or "outlook")
    salt = get_or_create_salt(salt_path)
    MailIdentityIndex(database_path)

    scan_tz_name = str((cfg.get("scan") or {}).get("timezone") or "Asia/Hong_Kong")
    try:
        scan_tz = ZoneInfo(scan_tz_name)
    except Exception:
        scan_tz = dt.datetime.now().astimezone().tzinfo
        scan_tz_name = str(scan_tz)

    # ---- mailboxes --------------------------------------------------------
    if args.mailbox:
        mailboxes = args.mailbox
    else:
        try:
            mailboxes_data = hjson(["himalaya", "mailbox", "list", "--json"], cfg, "mailbox list")
            mailboxes = [m.get("name") or m.get("id") for m in mailboxes_data.get("mailboxes", []) if (m.get("name") or m.get("id"))]
        except Exception as e:
            error = f"mailbox_list_failed: {e}"
            paths = write_error_logs(error)
            print(json.dumps({"ok": False, "scan_log_paths": paths, "error": error}, ensure_ascii=False))
            return 1

    concurrency_cfg = cfg.get("concurrency") or {}
    scan_workers = max(1, int(concurrency_cfg.get("mailbox_scan") or 1))
    index_workers = max(1, int(concurrency_cfg.get("index") or 4))
    concurrency_info = {"mailbox_scan": scan_workers, "index": index_workers}
    scan_delay, _ = throttle_delays(cfg)

    # ---- scan folders -----------------------------------------------------
    def scan_folder(folder: str) -> tuple[str, list[dict[str, Any]], str | None]:
        try:
            if scan_delay:
                time.sleep(scan_delay)
            data = hjson(
                ["himalaya", "envelope", "search", "-m", folder, "-s", str(args.limit_per_mailbox), "--json", search_query],
                cfg,
                f"scan {folder}",
            )
            envelopes = [e for e in data.get("envelopes", []) if envelope_in_window(e, scan_tz, first_day, window_end)]
            return folder, envelopes, None
        except Exception as e:
            return folder, [], str(e)[:500]

    folder_failures: list[tuple[int, dict[str, Any]]] = []
    pending: list[tuple[str, int, int, dict[str, Any]]] = []  # (day, folder_index, env_index, envelope)
    with ThreadPoolExecutor(max_workers=scan_workers) as scan_pool:
        scan_futures = {scan_pool.submit(scan_folder, folder): fi for fi, folder in enumerate(mailboxes)}
        for scan_fut in as_completed(scan_futures):
            fi = scan_futures[scan_fut]
            folder, envelopes, err = scan_fut.result()
            if err:
                folder_failures.append((fi, {"folder": folder, "error": err}))
                continue
            for ei, env in enumerate(envelopes):
                day = envelope_local_date(env, scan_tz) or first_day
                if day in out_dirs:
                    pending.append((day, fi, ei, env))

    print(
        f"[scan] folders={len(mailboxes)} failed={len(folder_failures)} messages={len(pending)} days={len(days)}",
        file=sys.stderr,
        flush=True,
    )

    # ---- create stable per-message workspaces ----------------------------
    progress_lock = threading.Lock()
    progress = {"done": 0}

    def job(item: tuple[str, int, int, dict[str, Any]]) -> tuple[str, int, int, dict[str, Any]]:
        day, fi, ei, env = item
        mail = register_scanned_envelope(
            mailboxes[fi],
            env,
            out_dirs[day],
            account=account_name,
            salt=salt,
            database_path=database_path,
            observed_date=day,
        )
        with progress_lock:
            progress["done"] += 1
            if progress["done"] % 25 == 0 or progress["done"] == len(pending):
                print(f"[progress] {progress['done']}/{len(pending)} messages", file=sys.stderr, flush=True)
        return day, fi, ei, mail

    results: dict[str, list[tuple[int, int, dict[str, Any]]]] = {d: [] for d in days}
    with ThreadPoolExecutor(max_workers=index_workers) as index_pool:
        message_futures = [index_pool.submit(job, item) for item in pending]
        for fut in as_completed(message_futures):
            day, fi, ei, mail = fut.result()
            results[day].append((fi, ei, mail))

    # ---- write immutable per-day scan logs --------------------------------
    mailboxes_failed_out = [e for _, e in sorted(folder_failures)]
    scan_log_paths: dict[str, str] = {}
    per_day: list[dict[str, Any]] = []
    total_messages = 0
    for d in days:
        ordered = sorted(results[d], key=lambda x: (x[0], x[1]))
        unique_messages: dict[str, dict[str, Any]] = {}
        for _, _, message in ordered:
            pipeline_id = message.get("pipeline_id")
            if pipeline_id:
                unique_messages[pipeline_id] = message
        pipeline_ids = list(unique_messages)
        rfc_message_ids = [str(unique_messages[pipeline_id]["rfc_message_id"]) for pipeline_id in pipeline_ids]
        pending_summary_pipeline_ids: list[str] = []
        reused_summary_pipeline_ids: list[str] = []
        for pipeline_id in pipeline_ids:
            state = classify_summary_state(database_path, out_dirs[d], pipeline_id)
            if state == "pending":
                pending_summary_pipeline_ids.append(pipeline_id)
            else:
                reused_summary_pipeline_ids.append(pipeline_id)
        generated_at = utc_now()
        scan_log: dict[str, Any] = {
            "schema_version": 1,
            "artifact_type": "mail_scan_log",
            "status": "completed",
            "date": d,
            "started_at": started_at,
            "completed_at": generated_at,
            "generated_at": generated_at,
            "range": f"HKT {d} 00:00 to {next_day(d)} 00:00 (half-open)",
            "account": account_name,
            "scan_scope": "all himalaya mailboxes including Canvas, Inbox, Carear Center, Junk Email, Deleted Items, __MINIMIZED/*",
            "timezone": scan_tz_name,
            "concurrency": concurrency_info,
            "mailboxes_total": len(mailboxes),
            "mailboxes_failed": mailboxes_failed_out,
            "messages_total": len(pipeline_ids),
            "rfc_message_ids": rfc_message_ids,
            "included_pipeline_ids": pipeline_ids,
            "pending_summary_pipeline_ids": pending_summary_pipeline_ids,
            "reused_summary_pipeline_ids": reused_summary_pipeline_ids,
        }
        if range_mode:
            scan_log["backfill"] = {"from": first_day, "to": last_day, "days": len(days)}
        log_dir = out_dirs[d] / "scan-log"
        scan_log_path = log_dir / scan_log_filename(generated_at)
        secure_write_text(scan_log_path, json.dumps(scan_log, ensure_ascii=False, indent=2))
        scan_log_paths[d] = str(scan_log_path)
        total_messages += len(pipeline_ids)
        per_day.append({
            "date": d,
            "scan_log_path": str(scan_log_path),
            "rfc_message_ids": rfc_message_ids,
            "messages_total": len(pipeline_ids),
            "summaries_pending": len(pending_summary_pipeline_ids),
            "summaries_reused": len(reused_summary_pipeline_ids),
        })

    if not range_mode:
        print(json.dumps({
            "ok": True,
            "scan_log_path": scan_log_paths[days[0]],
            "generated_at": json.loads(Path(scan_log_paths[days[0]]).read_text(encoding="utf-8"))["generated_at"],
            "mailboxes_total": len(mailboxes),
            "mailboxes_failed": mailboxes_failed_out,
            "messages_total": total_messages,
            "rfc_message_ids": rfc_message_ids,
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "ok": True,
            "mode": "range",
            "generated_at": generated_at,
            "from": first_day,
            "to": last_day,
            "days": len(days),
            "messages_total": total_messages,
            "mailboxes_total": len(mailboxes),
            "mailboxes_failed": mailboxes_failed_out,
            "per_day": per_day,
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
