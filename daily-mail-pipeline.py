#!/usr/bin/env python3
"""Compatibility orchestrator for unpack and summarize mail stages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "daily-mail-pipeline.yaml"
DEFAULT_OUTPUT_ROOT = Path.home() / ".hermes" / "email" / "daily"

SUMMARY_DISABLED = {
    "importance": "normal",
    "category": "other",
    "course": None,
    "deadlines": [],
    "action_required": None,
    "summary": "摘要阶段未运行。",
    "should_read_full": True,
    "error": "summary_disabled",
}


def secure_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        temp_path.chmod(0o600)
        os.replace(temp_path, path)
        path.chmod(0o600)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


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


def message_key(message: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(message.get("folder") or ""),
        str(message.get("id") or ""),
        str(message.get("message_id") or ""),
    )


def materialize_bundle(
    unpack_path: Path,
    summary_path: Path | None,
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    unpack_bytes = unpack_path.read_bytes()
    unpack = json.loads(unpack_bytes)
    summary: dict[str, Any] | None = None
    daily_summary: dict[str, Any]
    if summary_path:
        summary = json.loads(summary_path.read_bytes())
        daily_summary = dict(summary.get("daily_summary") or {})
    else:
        daily_summary = {
            "date": unpack.get("date"),
            "overview": "摘要阶段未运行。",
            "urgent_items": [],
            "events": [],
            "deadlines": [],
            "actions": [],
            "warnings": ["summary_disabled"],
            "messages_total": unpack.get("messages_total", 0),
            "messages_requiring_review": unpack.get("messages_total", 0),
        }

    message_index = [
        {
            "folder": item.get("folder"),
            "id": item.get("id"),
            "subject": item.get("subject"),
            "date": item.get("date"),
        }
        for item in unpack.get("messages") or []
    ]
    bundle = {
        "schema_version": 3,
        "artifact_type": "mail_digest",
        "date": unpack.get("date"),
        "generated_at": unpack.get("generated_at"),
        "messages_total": unpack.get("messages_total", len(message_index)),
        "mailboxes_total": unpack.get("mailboxes_total"),
        "mailboxes_failed": unpack.get("mailboxes_failed") or [],
        "daily_summary": daily_summary,
        "message_index": message_index,
        "summary_processor": summary.get("processor") if summary else None,
    }
    bundle_path = unpack_path.with_name("bundle.json")
    secure_write_text(bundle_path, json.dumps(bundle, ensure_ascii=False, indent=2))
    return bundle_path, bundle, daily_summary


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Unpack mail, optionally summarize it, and build bundle.json.")
    parser.add_argument("--date")
    parser.add_argument("--from", dest="date_from", metavar="YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", metavar="YYYY-MM-DD")
    parser.add_argument("--mailbox", action="append")
    parser.add_argument("--message-id", action="append")
    parser.add_argument("--limit-per-mailbox", type=int, default=200)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--no-summary", action="store_true")
    args = parser.parse_args()

    unpack_command = [
        sys.executable,
        str(SCRIPT_DIR / "unpack-mail.py"),
        "--config", str(args.config.expanduser()),
        "--output-root", str(args.output_root.expanduser()),
        "--limit-per-mailbox", str(args.limit_per_mailbox),
    ]
    for flag, value in (("--date", args.date), ("--from", args.date_from), ("--to", args.date_to)):
        if value:
            unpack_command.extend([flag, value])
    for mailbox in args.mailbox or []:
        unpack_command.extend(["--mailbox", mailbox])
    for message_id in args.message_id or []:
        unpack_command.extend(["--message-id", message_id])

    try:
        unpack_result = run_stage(unpack_command)
        unpack_paths = []
        if unpack_result.get("mode") == "range":
            unpack_paths = [Path(item["unpack_path"]) for item in unpack_result.get("per_day") or []]
        else:
            unpack_paths = [Path(unpack_result["unpack_path"])]

        per_day: list[dict[str, Any]] = []
        total_messages = 0
        for unpack_path in unpack_paths:
            summary_path: Path | None = None
            if not args.no_summary:
                summary_result = run_stage([
                    sys.executable,
                    str(SCRIPT_DIR / "summarize-mail.py"),
                    "--input", str(unpack_path),
                    "--config", str(args.config.expanduser()),
                ])
                summary_path = Path(summary_result["summary_path"])
            bundle_path, bundle, daily_summary = materialize_bundle(unpack_path, summary_path)
            total_messages += int(bundle.get("messages_total") or 0)
            per_day.append({
                "date": bundle.get("date"),
                "bundle_path": str(bundle_path),
                "unpack_path": str(unpack_path),
                "summary_path": str(summary_path) if summary_path else None,
                "messages_total": bundle.get("messages_total"),
                "daily_summary": daily_summary,
            })
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1

    if len(per_day) == 1:
        item = per_day[0]
        print(json.dumps({
            "ok": True,
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
                    "messages_total": item["messages_total"],
                    "daily_summary": item["daily_summary"],
                }
                for item in per_day
            ],
        }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
