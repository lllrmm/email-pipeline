"""Stable email identities, IMAP locations, queue state, and watcher state."""

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

from .program_time import now as program_now
from .program_time import now_rfc3339


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


def normalize_rfc_message_id(rfc_message_id: str) -> str:
    normalized = rfc_message_id.strip()
    if normalized.startswith("<") and normalized.endswith(">"):
        normalized = normalized[1:-1].strip()
    if not normalized:
        raise ValueError("RFC Message-ID is empty")
    return normalized


def make_pipeline_id(salt: bytes, rfc_message_id: str) -> str:
    normalized = normalize_rfc_message_id(rfc_message_id)
    return hashlib.sha256(salt + b"\0" + normalized.encode("utf-8")).hexdigest()


EMAIL_SCHEMA = """
CREATE TABLE IF NOT EXISTS email (
    pipeline_id TEXT PRIMARY KEY,
    rfc_message_id TEXT NOT NULL,
    identity_source TEXT NOT NULL CHECK(identity_source IN ('rfc_message_id', 'synthetic')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    eml_sha256 TEXT,
    summarized INTEGER NOT NULL DEFAULT 0,
    summarized_at TEXT,
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
    metadata_updated_at TEXT,
    workspace_date TEXT,
    workspace_path TEXT,
    workspace_updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_rfc ON email(rfc_message_id);
CREATE INDEX IF NOT EXISTS idx_email_sent_at ON email(sent_at);
CREATE INDEX IF NOT EXISTS idx_email_sender ON email(sender);
CREATE INDEX IF NOT EXISTS idx_email_subject ON email(subject);
CREATE TABLE IF NOT EXISTS email_location (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    imap_uid INTEGER NOT NULL,
    pipeline_id TEXT NOT NULL REFERENCES email(pipeline_id) ON DELETE CASCADE,
    last_seen TEXT NOT NULL,
    PRIMARY KEY(account, folder, uidvalidity, imap_uid)
);
CREATE INDEX IF NOT EXISTS idx_email_location_pipeline ON email_location(pipeline_id);
CREATE TABLE IF NOT EXISTS email_event_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    rfc_message_id TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    imap_uid INTEGER NOT NULL,
    sent_at TEXT,
    received_at TEXT,
    detected_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'processing', 'done')),
    attempts INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    completed_at TEXT,
    pipeline_id TEXT,
    last_error TEXT,
    UNIQUE(account, folder, uidvalidity, imap_uid)
);
CREATE INDEX IF NOT EXISTS idx_email_event_queue_status ON email_event_queue(status, queue_id);
CREATE TABLE IF NOT EXISTS email_watch_folder_state (
    account TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER NOT NULL,
    uidnext INTEGER NOT NULL,
    messages INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(account, folder)
);
"""


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
            tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "email_identity" in tables:
                self._migrate_legacy_schema(connection, tables)
            connection.executescript(EMAIL_SCHEMA)
            queue_columns = {row["name"] for row in connection.execute("PRAGMA table_info(email_event_queue)")}
            if "sent_at" not in queue_columns:
                connection.execute("ALTER TABLE email_event_queue ADD COLUMN sent_at TEXT")
            if "received_at" not in queue_columns:
                connection.execute("ALTER TABLE email_event_queue ADD COLUMN received_at TEXT")
            connection.commit()
        self.path.chmod(0o600)

    def _migrate_legacy_schema(self, connection: sqlite3.Connection, tables: set[str]) -> None:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executescript(EMAIL_SCHEMA.replace("email_location", "email_location_new"))
            identities = connection.execute("SELECT * FROM email_identity").fetchall()
            for identity in identities:
                pipeline_id = identity["pipeline_id"]
                metadata = connection.execute(
                    "SELECT * FROM email_metadata WHERE pipeline_id=?", (pipeline_id,)
                ).fetchone() if "email_metadata" in tables else None
                workspace = connection.execute(
                    "SELECT * FROM email_workspace WHERE pipeline_id=? ORDER BY observed_date, last_seen LIMIT 1",
                    (pipeline_id,),
                ).fetchone() if "email_workspace" in tables else None
                connection.execute(
                    """INSERT OR REPLACE INTO email (
                    pipeline_id,rfc_message_id,identity_source,first_seen,last_seen,eml_sha256,
                    summarized,summarized_at,sent_at,date_header,subject,sender,recipients,cc,bcc,
                    reply_to,in_reply_to,references_header,metadata_updated_at,workspace_date,
                    workspace_path,workspace_updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        pipeline_id, identity["rfc_message_id"], identity["identity_source"],
                        identity["first_seen"], identity["last_seen"], identity["eml_sha256"],
                        int(identity["summarized"]) if "summarized" in identity.keys() else 0,
                        identity["summarized_at"] if "summarized_at" in identity.keys() else None,
                        metadata["sent_at"] if metadata else None, metadata["date_header"] if metadata else None,
                        metadata["subject"] if metadata else None, metadata["sender"] if metadata else None,
                        metadata["recipients"] if metadata else None, metadata["cc"] if metadata else None,
                        metadata["bcc"] if metadata else None, metadata["reply_to"] if metadata else None,
                        metadata["in_reply_to"] if metadata else None,
                        metadata["references_header"] if metadata else None,
                        metadata["updated_at"] if metadata else None,
                        workspace["observed_date"] if workspace else None,
                        workspace["path"] if workspace else None,
                        workspace["last_seen"] if workspace else None,
                    ),
                )
            location_columns = {row["name"] for row in connection.execute("PRAGMA table_info(email_location)")}
            for row in connection.execute("SELECT * FROM email_location").fetchall():
                uidvalidity = row["uidvalidity"] if "uidvalidity" in location_columns else None
                imap_uid = row["imap_uid"] if "imap_uid" in location_columns else None
                if uidvalidity is None or imap_uid is None:
                    legacy = str(row["himalaya_id"] or "") if "himalaya_id" in location_columns else ""
                    if ":" in legacy:
                        left, right = legacy.split(":", 1)
                        if left.isdigit() and right.isdigit():
                            uidvalidity, imap_uid = int(left), int(right)
                if uidvalidity is None or imap_uid is None:
                    continue
                connection.execute(
                    """INSERT OR REPLACE INTO email_location_new
                    (account,folder,uidvalidity,imap_uid,pipeline_id,last_seen) VALUES (?,?,?,?,?,?)""",
                    (row["account"], row["folder"], int(uidvalidity), int(imap_uid), row["pipeline_id"], row["last_seen"]),
                )
            connection.execute("DROP TABLE email_location")
            connection.execute("ALTER TABLE email_location_new RENAME TO email_location")
            for table in ("email_workspace", "email_metadata", "email_identity"):
                if table in tables:
                    connection.execute(f"DROP TABLE {table}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.execute("PRAGMA foreign_keys=ON")

    def register_identity(self, *, pipeline_id: str, rfc_message_id: str, identity_source: str = "rfc_message_id") -> bool:
        rfc_message_id = normalize_rfc_message_id(rfc_message_id)
        timestamp = now_rfc3339()
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                """INSERT INTO email (pipeline_id,rfc_message_id,identity_source,first_seen,last_seen)
                VALUES (?,?,?,?,?) ON CONFLICT(pipeline_id) DO NOTHING""",
                (pipeline_id, rfc_message_id, identity_source, timestamp, timestamp),
            )
            created = cursor.rowcount == 1
            if not created:
                existing = connection.execute("SELECT rfc_message_id FROM email WHERE pipeline_id=?", (pipeline_id,)).fetchone()
                if existing is None or existing["rfc_message_id"] != rfc_message_id:
                    raise RuntimeError(f"pipeline ID collision for RFC Message-ID: {pipeline_id}")
                connection.execute("UPDATE email SET last_seen=? WHERE pipeline_id=?", (timestamp, pipeline_id))
            connection.commit()
        return created

    def record_imap_location(self, *, pipeline_id: str, account: str, folder: str, uidvalidity: int, uid: int) -> None:
        with closing(self.connect()) as connection:
            connection.execute(
                """INSERT INTO email_location (account,folder,uidvalidity,imap_uid,pipeline_id,last_seen)
                VALUES (?,?,?,?,?,?) ON CONFLICT(account,folder,uidvalidity,imap_uid) DO UPDATE SET
                pipeline_id=excluded.pipeline_id,last_seen=excluded.last_seen""",
                (account, folder, int(uidvalidity), int(uid), pipeline_id, now_rfc3339()),
            )
            connection.commit()

    def update_metadata(self, pipeline_id: str, **values: str | None) -> None:
        allowed = ("sent_at", "date_header", "subject", "sender", "recipients", "cc", "bcc", "reply_to", "in_reply_to", "references_header")
        updates = {key: values.get(key) for key in allowed if values.get(key) is not None}
        if not updates:
            return
        assignments = ",".join(f"{key}=COALESCE(?,{key})" for key in updates)
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                f"UPDATE email SET {assignments},metadata_updated_at=?,last_seen=? WHERE pipeline_id=?",
                (*updates.values(), now_rfc3339(), now_rfc3339(), pipeline_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown pipeline id: {pipeline_id}")
            connection.commit()

    def set_eml_sha256(self, pipeline_id: str, digest: str) -> None:
        with closing(self.connect()) as connection:
            connection.execute("UPDATE email SET eml_sha256=?,last_seen=? WHERE pipeline_id=?", (digest, now_rfc3339(), pipeline_id))
            connection.commit()

    def ensure_workspace(self, pipeline_id: str, workspace_date: str, path: Path) -> dict[str, str]:
        timestamp = now_rfc3339()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT workspace_date,workspace_path FROM email WHERE pipeline_id=?", (pipeline_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown pipeline id: {pipeline_id}")
            if row["workspace_path"]:
                result = {"date": str(row["workspace_date"]), "path": str(row["workspace_path"])}
            else:
                connection.execute(
                    "UPDATE email SET workspace_date=?,workspace_path=?,workspace_updated_at=?,last_seen=? WHERE pipeline_id=?",
                    (workspace_date, str(path), timestamp, timestamp, pipeline_id),
                )
                result = {"date": workspace_date, "path": str(path)}
            connection.commit()
        return result

    def set_summarized(self, pipeline_id: str, summarized: bool = True) -> None:
        timestamp = now_rfc3339()
        with closing(self.connect()) as connection:
            cursor = connection.execute(
                "UPDATE email SET summarized=?,summarized_at=?,last_seen=? WHERE pipeline_id=?",
                (int(summarized), timestamp if summarized else None, timestamp, pipeline_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown pipeline id: {pipeline_id}")
            connection.commit()

    def enqueue_event(self, *, account: str, rfc_message_id: str, folder: str, uidvalidity: int, uid: int, received_at: str | None = None, requeue: bool = False) -> bool:
        timestamp = now_rfc3339()
        rfc_message_id = normalize_rfc_message_id(rfc_message_id)
        with closing(self.connect()) as connection:
            if requeue:
                cursor = connection.execute(
                    """INSERT INTO email_event_queue (account,rfc_message_id,folder,uidvalidity,imap_uid,received_at,detected_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(account,folder,uidvalidity,imap_uid) DO UPDATE SET
                    received_at=COALESCE(excluded.received_at,email_event_queue.received_at),status='pending',
                    completed_at=NULL,claimed_at=NULL,last_error=NULL""",
                    (account, rfc_message_id, folder, int(uidvalidity), int(uid), received_at, timestamp),
                )
            else:
                cursor = connection.execute(
                    """INSERT INTO email_event_queue (account,rfc_message_id,folder,uidvalidity,imap_uid,received_at,detected_at)
                    VALUES (?,?,?,?,?,?,?) ON CONFLICT(account,folder,uidvalidity,imap_uid) DO NOTHING""",
                    (account, rfc_message_id, folder, int(uidvalidity), int(uid), received_at, timestamp),
                )
            connection.commit()
        return cursor.rowcount == 1

    def claim_events(self, limit: int = 20, stale_seconds: int = 900) -> list[dict[str, Any]]:
        current = program_now()
        stale = (current - dt.timedelta(seconds=stale_seconds)).isoformat(timespec="seconds")
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE email_event_queue SET status='pending',claimed_at=NULL WHERE status='processing' AND claimed_at<?", (stale,))
            rows = connection.execute("SELECT * FROM email_event_queue WHERE status='pending' AND attempts<5 ORDER BY queue_id LIMIT ?", (max(1, int(limit)),)).fetchall()
            ids = [int(row["queue_id"]) for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(f"UPDATE email_event_queue SET status='processing',attempts=attempts+1,claimed_at=?,last_error=NULL WHERE queue_id IN ({placeholders})", (current.isoformat(timespec="seconds"), *ids))
            connection.commit()
        return [dict(row) for row in rows]

    def complete_events(self, queue_ids: list[int], pipeline_ids: dict[int, str]) -> None:
        with closing(self.connect()) as connection:
            for queue_id in queue_ids:
                connection.execute("UPDATE email_event_queue SET status='done',completed_at=?,pipeline_id=? WHERE queue_id=?", (now_rfc3339(), pipeline_ids.get(queue_id), int(queue_id)))
            connection.commit()

    def retry_events(self, queue_ids: list[int], error: str) -> None:
        with closing(self.connect()) as connection:
            for queue_id in queue_ids:
                connection.execute("UPDATE email_event_queue SET status='pending',claimed_at=NULL,last_error=? WHERE queue_id=?", (error[:1000], int(queue_id)))
            connection.commit()

    def list_queue_events(self) -> list[dict[str, Any]]:
        with closing(self.connect()) as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM email_event_queue ORDER BY queue_id").fetchall()]

    def load_watch_snapshot(self, account: str) -> dict[str, dict[str, int]]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT folder,uidvalidity,uidnext,messages FROM email_watch_folder_state WHERE account=?", (account,)).fetchall()
        return {str(row["folder"]): {"uidvalidity": int(row["uidvalidity"]), "uidnext": int(row["uidnext"]), "messages": int(row["messages"])} for row in rows}

    def replace_watch_snapshot(self, account: str, snapshot: dict[str, dict[str, int]]) -> None:
        timestamp = now_rfc3339()
        with closing(self.connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM email_watch_folder_state WHERE account=?", (account,))
            connection.executemany("INSERT INTO email_watch_folder_state (account,folder,uidvalidity,uidnext,messages,updated_at) VALUES (?,?,?,?,?,?)", [(account, folder, int(values["uidvalidity"]), int(values["uidnext"]), int(values.get("messages", 0)), timestamp) for folder, values in snapshot.items()])
            connection.commit()

    def lookup_pipeline_id(self, pipeline_id: str) -> dict[str, Any] | None:
        return self._lookup(pipeline_id)

    def lookup_rfc_message_id(self, rfc_message_id: str) -> list[dict[str, Any]]:
        normalized = normalize_rfc_message_id(rfc_message_id)
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT pipeline_id FROM email WHERE rfc_message_id=? ORDER BY first_seen", (normalized,)).fetchall()
        return [result for row in rows if (result := self._lookup(row["pipeline_id"]))]

    def _lookup(self, pipeline_id: str) -> dict[str, Any] | None:
        with closing(self.connect()) as connection:
            row = connection.execute("SELECT * FROM email WHERE pipeline_id=?", (pipeline_id,)).fetchone()
            if row is None:
                return None
            locations = connection.execute("SELECT account,folder,uidvalidity,imap_uid,last_seen FROM email_location WHERE pipeline_id=? ORDER BY last_seen DESC,imap_uid DESC", (pipeline_id,)).fetchall()
        result = dict(row)
        result["summarized"] = bool(result["summarized"])
        result["locations"] = [dict(location) for location in locations]
        result["metadata"] = {key: result.get(key) for key in ("sent_at", "date_header", "subject", "sender", "recipients", "cc", "bcc", "reply_to", "in_reply_to", "references_header", "metadata_updated_at")}
        result["workspace"] = {"date": result["workspace_date"], "path": result["workspace_path"]} if result.get("workspace_path") else None
        return result


def write_json(path: Path, value: Any) -> None:
    secure_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
