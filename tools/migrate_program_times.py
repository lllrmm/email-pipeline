#!/usr/bin/env python3
"""Normalize persisted pipeline-owned timestamps to the configured timezone."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1]
if str(SCRIPT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR / "src"))

from email_pipeline.config import load_config  # noqa: E402


PROGRAM_TIME_KEYS = {
    "generated_at",
    "started_at",
    "completed_at",
    "detected_at",
    "claimed_at",
    "received_at",
    "earliest_email_at",
    "latest_email_at",
    "from_time",
    "to_time",
}
DATABASE_COLUMNS = {
    "email_identity": ("first_seen", "last_seen", "summarized_at"),
    "email_location": ("last_seen",),
    "email_workspace": ("last_seen",),
    "email_metadata": ("updated_at",),
    "email_event_queue": ("received_at", "detected_at", "claimed_at", "completed_at"),
    "email_watch_state": ("updated_at",),
}
LOG_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(Z|[+-]\d{2}:?\d{2})(?=\s)")


def convert(value: str, timezone: ZoneInfo) -> str:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone offset")
    return parsed.astimezone(timezone).isoformat(timespec="seconds")


def rewrite_json_value(value: Any, timezone: ZoneInfo) -> int:
    changed = 0
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PROGRAM_TIME_KEYS and isinstance(child, str):
                try:
                    normalized = convert(child, timezone)
                except ValueError:
                    continue
                if normalized != child:
                    value[key] = normalized
                    changed += 1
            elif isinstance(child, (dict, list)):
                changed += rewrite_json_value(child, timezone)
    elif isinstance(value, list):
        for child in value:
            changed += rewrite_json_value(child, timezone)
    return changed


def atomic_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def migrate_database(path: Path, timezone: ZoneInfo) -> int:
    backup = path.with_suffix(path.suffix + ".pre-program-time")
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    changed = 0
    connection = sqlite3.connect(path)
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, columns in DATABASE_COLUMNS.items():
            if table not in tables:
                continue
            available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            for column in columns:
                if column not in available:
                    continue
                rows = connection.execute(
                    f"SELECT rowid, {column} FROM {table} WHERE {column} IS NOT NULL"
                ).fetchall()
                for rowid, value in rows:
                    try:
                        normalized = convert(str(value), timezone)
                    except ValueError:
                        continue
                    if normalized != value:
                        connection.execute(
                            f"UPDATE {table} SET {column}=? WHERE rowid=?", (normalized, rowid)
                        )
                        changed += 1
        connection.commit()
    finally:
        connection.close()
    return changed


def migrate_json_files(root: Path, timezone: ZoneInfo) -> tuple[int, int]:
    files = fields = 0
    for path in root.rglob("*.json"):
        if "opencode-run" in path.parts or path.name in {"request.json", "manifest.json"}:
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        changed = rewrite_json_value(value, timezone)
        if changed:
            atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            files += 1
            fields += changed
    return files, fields


def migrate_logs(root: Path, timezone: ZoneInfo) -> tuple[int, int]:
    files = lines = 0
    for path in root.glob("*/logs/*/*.log"):
        changed = 0
        output = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            match = LOG_PREFIX.match(line)
            if match:
                try:
                    prefix = convert("".join(match.groups()), timezone)
                except ValueError:
                    pass
                else:
                    line = prefix + line[match.end():]
                    changed += 1
            output.append(line)
        if changed:
            atomic_write(path, "\n".join(output) + "\n")
            files += 1
            lines += changed
    return files, lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    args = parser.parse_args()
    config = load_config(args.config.expanduser().resolve())
    timezone = ZoneInfo(str(config.get("timezone") or "UTC"))
    identity = config.get("identity") or {}
    database = Path(identity.get("database_path") or (Path.home() / ".hermes/email/mail-index.sqlite3")).expanduser().resolve()
    root = args.output_root.expanduser().resolve()
    database_fields = migrate_database(database, timezone)
    json_files, json_fields = migrate_json_files(root, timezone)
    log_files, log_lines = migrate_logs(root, timezone)
    print(json.dumps({
        "ok": True,
        "timezone": str(timezone),
        "database_fields": database_fields,
        "json_files": json_files,
        "json_fields": json_fields,
        "log_files": log_files,
        "log_lines": log_lines,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
