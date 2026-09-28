"""Stable salted email identities and SQLite location index."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any


def secure_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp_name, path)
        path.chmod(0o600)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def get_or_create_salt(path: Path) -> bytes:
    if path.exists():
        value = bytes.fromhex(path.read_text(encoding="ascii").strip())
        if len(value) < 32:
            raise RuntimeError(f"pipeline id salt is too short: {path}")
        return value
    value = secrets.token_bytes(32)
    secure_write(path, (value.hex() + "\n").encode("ascii"))
    return value


def make_pipeline_id(salt: bytes, rfc_message_id: str) -> str:
    normalized = rfc_message_id.strip()
    if not normalized:
        raise ValueError("RFC Message-ID is empty")
    return hashlib.sha256(salt + b"\0" + normalized.encode("utf-8", errors="strict")).hexdigest()


def make_synthetic_identity(account: str, folder: str, himalaya_id: str, date: str) -> str:
    return f"synthetic:{account}\0{folder}\0{himalaya_id}\0{date}"


class MailIdentityIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with closing(self.connect()) as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS email_identity (
                    pipeline_id TEXT PRIMARY KEY,
                    rfc_message_id TEXT,
                    identity_source TEXT NOT NULL CHECK(identity_source IN ('rfc_message_id', 'synthetic')),
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    eml_sha256 TEXT,
                    summarized INTEGER NOT NULL DEFAULT 0,
                    summarized_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_email_identity_rfc
                    ON email_identity(rfc_message_id);
                CREATE TABLE IF NOT EXISTS email_location (
                    account TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    himalaya_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL REFERENCES email_identity(pipeline_id) ON DELETE CASCADE,
                    observed_date TEXT,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY(account, folder, himalaya_id)
                );
                CREATE INDEX IF NOT EXISTS idx_email_location_pipeline
                    ON email_location(pipeline_id);
                CREATE TABLE IF NOT EXISTS email_workspace (
                    pipeline_id TEXT NOT NULL REFERENCES email_identity(pipeline_id) ON DELETE CASCADE,
                    observed_date TEXT NOT NULL,
                    path TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY(pipeline_id, observed_date)
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(email_identity)").fetchall()
            }
            if "summarized" not in columns:
                connection.execute("ALTER TABLE email_identity ADD COLUMN summarized INTEGER NOT NULL DEFAULT 0")
            if "summarized_at" not in columns:
                connection.execute("ALTER TABLE email_identity ADD COLUMN summarized_at TEXT")
            connection.commit()
        self.path.chmod(0o600)

    def record(
        self,
        *,
        pipeline_id: str,
        rfc_message_id: str | None,
        identity_source: str,
        account: str,
        folder: str,
        himalaya_id: str,
        observed_date: str,
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO email_identity
                    (pipeline_id, rfc_message_id, identity_source, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id) DO UPDATE SET
                    rfc_message_id=COALESCE(excluded.rfc_message_id, email_identity.rfc_message_id),
                    last_seen=excluded.last_seen
                """,
                (pipeline_id, rfc_message_id, identity_source, now, now),
            )
            connection.execute(
                """
                INSERT INTO email_location
                    (account, folder, himalaya_id, pipeline_id, observed_date, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account, folder, himalaya_id) DO UPDATE SET
                    pipeline_id=excluded.pipeline_id,
                    observed_date=excluded.observed_date,
                    last_seen=excluded.last_seen
                """,
                (account, folder, himalaya_id, pipeline_id, observed_date, now),
            )
            connection.commit()

    def set_eml_sha256(self, pipeline_id: str, digest: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                "UPDATE email_identity SET eml_sha256=?, last_seen=? WHERE pipeline_id=?",
                (digest, dt.datetime.now(dt.timezone.utc).isoformat(), pipeline_id),
            )
            connection.commit()

    def record_workspace(self, pipeline_id: str, observed_date: str, path: Path) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            connection.execute(
                """
                INSERT INTO email_workspace (pipeline_id, observed_date, path, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(pipeline_id, observed_date) DO UPDATE SET
                    path=excluded.path,
                    last_seen=excluded.last_seen
                """,
                (pipeline_id, observed_date, str(path), now),
            )
            connection.commit()

    def set_summarized(self, pipeline_id: str, summarized: bool = True) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE email_identity
                SET summarized=?, summarized_at=?, last_seen=?
                WHERE pipeline_id=?
                """,
                (1 if summarized else 0, now if summarized else None, now, pipeline_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown pipeline id: {pipeline_id}")
            connection.commit()

    def lookup_pipeline_id(self, pipeline_id: str) -> dict[str, Any] | None:
        return self._lookup("pipeline_id", pipeline_id)

    def lookup_rfc_message_id(self, rfc_message_id: str) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            rows = connection.execute(
                "SELECT pipeline_id FROM email_identity WHERE rfc_message_id=? ORDER BY first_seen",
                (rfc_message_id.strip(),),
            ).fetchall()
        return [result for row in rows if (result := self.lookup_pipeline_id(row["pipeline_id"]))]

    def _lookup(self, field: str, value: str) -> dict[str, Any] | None:
        if field != "pipeline_id":
            raise ValueError("unsupported lookup field")
        with closing(self.connect()) as connection:
            row = connection.execute(
                "SELECT * FROM email_identity WHERE pipeline_id=?", (value,)
            ).fetchone()
            if row is None:
                return None
            locations = connection.execute(
                """
                SELECT account, folder, himalaya_id, observed_date, last_seen
                FROM email_location WHERE pipeline_id=? ORDER BY last_seen DESC
                """,
                (value,),
            ).fetchall()
            workspaces = connection.execute(
                """
                SELECT observed_date, path, last_seen
                FROM email_workspace WHERE pipeline_id=? ORDER BY observed_date DESC
                """,
                (value,),
            ).fetchall()
        result = dict(row)
        result["summarized"] = bool(result.get("summarized"))
        result["locations"] = [dict(location) for location in locations]
        result["workspaces"] = [dict(workspace) for workspace in workspaces]
        return result


def write_json(path: Path, value: Any) -> None:
    secure_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
