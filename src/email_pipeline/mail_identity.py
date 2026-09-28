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
                CREATE TABLE IF NOT EXISTS email_metadata (
                    pipeline_id TEXT PRIMARY KEY REFERENCES email_identity(pipeline_id) ON DELETE CASCADE,
                    sent_at TEXT,
                    date_header TEXT,
                    subject TEXT,
                    sender TEXT,
                    recipients TEXT,
                    cc TEXT,
                    bcc TEXT,
                    reply_to TEXT,
                    in_reply_to TEXT,
                    references_header TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_email_metadata_sent_at
                    ON email_metadata(sent_at);
                CREATE INDEX IF NOT EXISTS idx_email_metadata_sender
                    ON email_metadata(sender);
                CREATE INDEX IF NOT EXISTS idx_email_metadata_subject
                    ON email_metadata(subject);
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
        observed_date: str | None,
        sent_at: str | None = None,
        subject: str | None = None,
        sender: str | None = None,
    ) -> None:
        self.register_identity(
            pipeline_id=pipeline_id,
            rfc_message_id=rfc_message_id,
            identity_source=identity_source,
        )
        self.record_location(
            pipeline_id=pipeline_id,
            account=account,
            folder=folder,
            himalaya_id=himalaya_id,
            observed_date=observed_date,
        )
        if any(value is not None for value in (sent_at, subject, sender)):
            self.update_metadata(
                pipeline_id,
                sent_at=sent_at,
                subject=subject,
                sender=sender,
            )

    def register_identity(
        self,
        *,
        pipeline_id: str,
        rfc_message_id: str,
        identity_source: str = "rfc_message_id",
    ) -> bool:
        """Register one stable identity and return True only when newly inserted."""
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO email_identity
                    (pipeline_id, rfc_message_id, identity_source, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(pipeline_id) DO NOTHING
                """,
                (pipeline_id, rfc_message_id, identity_source, now, now),
            )
            created = cursor.rowcount == 1
            if not created:
                existing = connection.execute(
                    "SELECT rfc_message_id FROM email_identity WHERE pipeline_id=?",
                    (pipeline_id,),
                ).fetchone()
                if existing is None:
                    raise RuntimeError(f"identity registration failed: {pipeline_id}")
                existing_rfc = str(existing["rfc_message_id"] or "").strip()
                if existing_rfc and existing_rfc != rfc_message_id.strip():
                    raise RuntimeError(f"pipeline ID collision for RFC Message-ID: {pipeline_id}")
                connection.execute(
                    """
                    UPDATE email_identity
                    SET rfc_message_id=COALESCE(rfc_message_id, ?), last_seen=?
                    WHERE pipeline_id=?
                    """,
                    (rfc_message_id, now, pipeline_id),
                )
            connection.commit()
        return created

    def record_location(
        self,
        *,
        pipeline_id: str,
        account: str,
        folder: str,
        himalaya_id: str,
        observed_date: str,
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
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

    @staticmethod
    def _upsert_metadata(
        connection: sqlite3.Connection,
        *,
        pipeline_id: str,
        updated_at: str,
        sent_at: str | None = None,
        date_header: str | None = None,
        subject: str | None = None,
        sender: str | None = None,
        recipients: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        reply_to: str | None = None,
        in_reply_to: str | None = None,
        references_header: str | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO email_metadata (
                pipeline_id, sent_at, date_header, subject, sender, recipients,
                cc, bcc, reply_to, in_reply_to, references_header, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pipeline_id) DO UPDATE SET
                sent_at=COALESCE(excluded.sent_at, email_metadata.sent_at),
                date_header=COALESCE(excluded.date_header, email_metadata.date_header),
                subject=COALESCE(excluded.subject, email_metadata.subject),
                sender=COALESCE(excluded.sender, email_metadata.sender),
                recipients=COALESCE(excluded.recipients, email_metadata.recipients),
                cc=COALESCE(excluded.cc, email_metadata.cc),
                bcc=COALESCE(excluded.bcc, email_metadata.bcc),
                reply_to=COALESCE(excluded.reply_to, email_metadata.reply_to),
                in_reply_to=COALESCE(excluded.in_reply_to, email_metadata.in_reply_to),
                references_header=COALESCE(excluded.references_header, email_metadata.references_header),
                updated_at=excluded.updated_at
            """,
            (
                pipeline_id, sent_at, date_header, subject, sender, recipients,
                cc, bcc, reply_to, in_reply_to, references_header, updated_at,
            ),
        )

    def update_metadata(
        self,
        pipeline_id: str,
        *,
        sent_at: str | None = None,
        date_header: str | None = None,
        subject: str | None = None,
        sender: str | None = None,
        recipients: str | None = None,
        cc: str | None = None,
        bcc: str | None = None,
        reply_to: str | None = None,
        in_reply_to: str | None = None,
        references_header: str | None = None,
    ) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        with closing(self.connect()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM email_identity WHERE pipeline_id=?", (pipeline_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"unknown pipeline id: {pipeline_id}")
            self._upsert_metadata(
                connection,
                pipeline_id=pipeline_id,
                sent_at=sent_at,
                date_header=date_header,
                subject=subject,
                sender=sender,
                recipients=recipients,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
                in_reply_to=in_reply_to,
                references_header=references_header,
                updated_at=now,
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
            metadata = connection.execute(
                "SELECT * FROM email_metadata WHERE pipeline_id=?", (value,)
            ).fetchone()
        result = dict(row)
        result["summarized"] = bool(result.get("summarized"))
        result["locations"] = [dict(location) for location in locations]
        result["workspaces"] = [dict(workspace) for workspace in workspaces]
        result["metadata"] = dict(metadata) if metadata is not None else None
        return result


def write_json(path: Path, value: Any) -> None:
    secure_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
