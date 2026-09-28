#!/usr/bin/env python3
"""Orchestrate mail scanning, per-email analysis, and daily aggregation."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml

SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR / "src"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from email_pipeline.mail_identity import MailIdentityIndex  # noqa: E402
from email_pipeline.mime_extract import secure_write_text  # noqa: E402

DEFAULT_CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"
DEFAULT_OUTPUT_ROOT = Path.home() / ".hermes" / "email" / "daily"

def run_stage(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.stderr:
        print(completed.stderr, file=sys.stderr, end="")
    if completed.returncode != 0:
        raise RuntimeError(
            f"stage failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stdout[-1000:]}"
        )
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"stage returned invalid JSON: {completed.stdout[-1000:]}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"stage reported failure: {result}")
    return result


def prepare_scanned_mails(scan_log_path: Path, config_path: Path) -> tuple[list[str], list[str], list[str]]:
    scan_log = json.loads(scan_log_path.read_text(encoding="utf-8"))
    mails = list(scan_log.get("mails") or [])
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    identity_config = config.get("identity") or {}
    database_path = Path(identity_config.get("database_path") or (Path.home() / ".hermes" / "email" / "mail-index.sqlite3")).expanduser().resolve()
    index = MailIdentityIndex(database_path)
    day_dir = scan_log_path.parent.parent
    pipeline_ids: list[str] = []
    rfc_by_pipeline: dict[str, str] = {}
    for mail in mails:
        rfc_message_id = str(mail.get("rfc_message_id") or "").strip()
        folder = str(mail.get("folder") or "").strip()
        uidvalidity = str(mail.get("uidvalidity") or "").strip()
        uid = str(mail.get("uid") or "").strip()
        result = run_stage([
            sys.executable,
            str(SCRIPT_DIR / "index_mail.py"),
            "--rfc-message-id", rfc_message_id,
            "--folder", folder,
            "--uidvalidity", uidvalidity,
            "--uid", uid,
            "--config", str(config_path),
        ])
        pipeline_id = str(result["pipeline_id"])
        if pipeline_id not in rfc_by_pipeline:
            pipeline_ids.append(pipeline_id)
            rfc_by_pipeline[pipeline_id] = rfc_message_id

    pending: list[str] = []
    reused: list[str] = []
    for pipeline_id in pipeline_ids:
        mail_dir = day_dir / "emails" / pipeline_id
        mail_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        index.record_workspace(pipeline_id, str(scan_log.get("date") or day_dir.name), mail_dir)
        secure_write_text(mail_dir / "request.json", json.dumps({
            "pipeline_id": pipeline_id,
            "rfc_message_id": rfc_by_pipeline[pipeline_id],
            "identity_source": "rfc_message_id",
            "index_database": str(database_path),
        }, ensure_ascii=False, indent=2))
        identity = index.lookup_pipeline_id(pipeline_id)
        if identity is None or identity.get("summarized") is not True:
            pending.append(pipeline_id)
            continue
        summary_path = mail_dir / "summary.json"
        if not summary_path.is_file():
            raise RuntimeError(f"database says summarized but summary.json is missing: {pipeline_id}")
        artifact = json.loads(summary_path.read_text(encoding="utf-8"))
        if artifact.get("pipeline_id") != pipeline_id or not isinstance(artifact.get("analysis"), dict):
            raise RuntimeError(f"database says summarized but summary.json is invalid: {pipeline_id}")
        reused.append(pipeline_id)
    return pipeline_ids, pending, reused


def run_agentic_summaries(
    scan_log_path: Path,
    config_path: Path,
    pipeline_ids: list[str],
    pending_ids: list[str],
    reused_ids: list[str],
    aggregate: bool = True,
) -> tuple[Path | None, int, int]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    workers = max(1, int((config.get("opencode") or {}).get("concurrency") or 8))
    day_dir = scan_log_path.parent.parent
    completed_ids: list[str | None] = [None] * len(pending_ids)

    def job(position: int, pipeline_id: str) -> tuple[int, str]:
        workdir = (day_dir / "emails" / pipeline_id).resolve()
        output_path = workdir / "summary.json"
        run_stage([
            sys.executable,
            str(SCRIPT_DIR / "summarize-mail-agentic.py"),
            "--pipeline-id", pipeline_id,
            "--mail-dir", str(workdir),
            "--output", str(output_path),
            "--config", str(config_path),
        ])
        return position, pipeline_id

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(job, position, pipeline_id) for position, pipeline_id in enumerate(pending_ids)]
        for future in as_completed(futures):
            position, pipeline_id = future.result()
            completed_ids[position] = pipeline_id

    completed_pipeline_ids = [pipeline_id for pipeline_id in completed_ids if pipeline_id]
    if len(completed_pipeline_ids) != len(pending_ids):
        raise RuntimeError("not every pending email summary completed")
    if not aggregate:
        return None, len(reused_ids), len(completed_pipeline_ids)
    aggregation_dir = day_dir / "aggregation"
    result = run_stage([
        sys.executable,
        str(SCRIPT_DIR / "aggregate-mails-agentic.py"),
        "--pipeline-id-list", *pipeline_ids,
        "--agent-workdir", str(day_dir),
        "--output-dir", str(aggregation_dir),
        "--config", str(config_path),
    ])
    return Path(result["summary_path"]), len(reused_ids), len(completed_pipeline_ids)


def load_daily_summary(scan_log: dict[str, Any], aggregation_path: Path | None) -> dict[str, Any]:
    if aggregation_path:
        aggregation = json.loads(aggregation_path.read_text(encoding="utf-8"))
        return dict(aggregation.get("daily_summary") or {})
    messages_total = int(scan_log.get("messages_total") or 0)
    return {
        "date": scan_log.get("date"),
        "overview": "摘要阶段未运行。",
        "events": [],
        "warnings": ["summary_disabled"],
        "messages_total": messages_total,
        "messages_requiring_review": messages_total,
    }


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Index mail, run per-email agents, aggregate, and publish the daily digest.")
    parser.add_argument("--date")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD")
    parser.add_argument("--mailbox", action="append")
    parser.add_argument("--limit-per-mailbox", type=int, default=200)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-summary", action="store_true")
    parser.add_argument("--no-aggregation", action="store_true")
    args = parser.parse_args()

    index_command = [
        sys.executable,
        str(SCRIPT_DIR / "scan_mails.py"),
        "--config", str(args.config.expanduser()),
        "--output-root", str(args.output_root.expanduser()),
        "--limit-per-mailbox", str(args.limit_per_mailbox),
    ]
    for flag, value in (("--date", args.date), ("--from", args.date_from), ("--to", args.date_to)):
        if value:
            index_command.extend([flag, value])
    for mailbox in args.mailbox or []:
        index_command.extend(["--mailbox", mailbox])

    try:
        index_result = run_stage(index_command)
        scan_log_paths = []
        if index_result.get("mode") == "range":
            scan_log_paths = [Path(item["scan_log_path"]) for item in index_result.get("per_day") or []]
        else:
            scan_log_paths = [Path(index_result["scan_log_path"])]

        per_day: list[dict[str, Any]] = []
        total_messages = 0
        for scan_log_path in scan_log_paths:
            scan_log = json.loads(scan_log_path.read_text(encoding="utf-8"))
            pipeline_ids, pending_ids, reused_ids = prepare_scanned_mails(
                scan_log_path,
                args.config.expanduser().resolve(),
            )
            aggregation_path: Path | None = None
            summaries_reused = len(reused_ids)
            summaries_created = 0
            if not args.no_summary:
                aggregation_path, summaries_reused, summaries_created = run_agentic_summaries(
                    scan_log_path,
                    args.config.expanduser().resolve(),
                    pipeline_ids,
                    pending_ids,
                    reused_ids,
                    aggregate=not args.no_aggregation,
                )
            daily_summary = load_daily_summary(scan_log, aggregation_path)
            messages_total = int(scan_log.get("messages_total") or 0)
            total_messages += messages_total
            per_day.append({
                "date": scan_log.get("date"),
                "scan_log_path": str(scan_log_path),
                "aggregation_path": str(aggregation_path) if aggregation_path else None,
                "scan_generated_at": scan_log.get("generated_at"),
                "mailboxes_total": scan_log.get("mailboxes_total"),
                "mailboxes_failed": scan_log.get("mailboxes_failed") or [],
                "summaries_reused": summaries_reused,
                "summaries_created": summaries_created,
                "messages_total": messages_total,
                "daily_summary": daily_summary,
            })
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    if len(per_day) == 1:
        item = per_day[0]
        print(json.dumps({
            "ok": True,
            "scan_generated_at": item["scan_generated_at"],
            "mailboxes_total": item["mailboxes_total"],
            "mailboxes_failed": item["mailboxes_failed"],
            "summaries_reused": item["summaries_reused"],
            "summaries_created": item["summaries_created"],
            "messages_total": item["messages_total"],
            "daily_summary": item["daily_summary"],
        }, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({
            "ok": True,
            "mode": "range",
            "messages_total": total_messages,
            "per_day": [
                {
                    "date": item["date"],
                    "scan_generated_at": item["scan_generated_at"],
                    "mailboxes_total": item["mailboxes_total"],
                    "mailboxes_failed": item["mailboxes_failed"],
                    "summaries_reused": item["summaries_reused"],
                    "summaries_created": item["summaries_created"],
                    "messages_total": item["messages_total"],
                    "daily_summary": item["daily_summary"],
                }
                for item in per_day
            ],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
