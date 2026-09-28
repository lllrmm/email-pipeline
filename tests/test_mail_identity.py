from __future__ import annotations

import tempfile
import unittest
import importlib.util
import sqlite3
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex, get_or_create_salt, make_pipeline_id

ROOT = Path(__file__).resolve().parents[1]
SCAN_SPEC = importlib.util.spec_from_file_location("scan_mails", ROOT / "scan_mails.py")
assert SCAN_SPEC and SCAN_SPEC.loader
SCAN_MAILS = importlib.util.module_from_spec(SCAN_SPEC)
SCAN_SPEC.loader.exec_module(SCAN_MAILS)
INDEX_SPEC = importlib.util.spec_from_file_location("index_mail", ROOT / "index_mail.py")
assert INDEX_SPEC and INDEX_SPEC.loader
INDEX_MAIL = importlib.util.module_from_spec(INDEX_SPEC)
INDEX_SPEC.loader.exec_module(INDEX_MAIL)


class MailIdentityTests(unittest.TestCase):
    def test_scan_log_filename_uses_generated_utc_time(self) -> None:
        filename = SCAN_MAILS.scan_log_filename("2026-09-28T10:04:35.098404Z")

        self.assertEqual(filename, "scan-20260928T100435.098404Z.json")

    def test_existing_database_is_migrated_with_metadata_table(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "legacy.sqlite3"
            connection = sqlite3.connect(database)
            connection.executescript(
                """
                CREATE TABLE email_identity (
                    pipeline_id TEXT PRIMARY KEY,
                    rfc_message_id TEXT,
                    identity_source TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    eml_sha256 TEXT,
                    summarized INTEGER NOT NULL DEFAULT 0,
                    summarized_at TEXT
                );
                CREATE TABLE email_location (
                    account TEXT NOT NULL,
                    folder TEXT NOT NULL,
                    himalaya_id TEXT NOT NULL,
                    pipeline_id TEXT NOT NULL,
                    observed_date TEXT,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY(account, folder, himalaya_id)
                );
                CREATE TABLE email_workspace (
                    pipeline_id TEXT NOT NULL,
                    observed_date TEXT NOT NULL,
                    path TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    PRIMARY KEY(pipeline_id, observed_date)
                );
                """
            )
            connection.close()

            MailIdentityIndex(database)
            connection = sqlite3.connect(database)
            metadata_table = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='email_metadata'"
            ).fetchone()
            connection.close()

            self.assertEqual(metadata_table, ("email_metadata",))

    def test_missing_rfc_message_id_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(RuntimeError, "RFC Message-ID is required"):
                INDEX_MAIL.register_mail(
                    "",
                    "Inbox",
                    123,
                    42,
                    salt=b"x" * 32,
                    database_path=root / "index.sqlite3",
                )

    def test_index_mail_registers_only_rfc_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            database = root / "index.sqlite3"
            first = INDEX_MAIL.register_mail(
                "<registered@example.com>",
                "Inbox",
                123,
                42,
                salt=b"x" * 32,
                database_path=database,
            )
            second = INDEX_MAIL.register_mail(
                "<registered@example.com>",
                "Inbox",
                123,
                42,
                salt=b"x" * 32,
                database_path=database,
            )

            identity = MailIdentityIndex(database).lookup_pipeline_id(first["pipeline_id"])

            self.assertEqual(first["status"], "registered")
            self.assertEqual(second["status"], "already_registered")
            self.assertEqual(first["pipeline_id"], second["pipeline_id"])
            self.assertEqual(identity["rfc_message_id"], "registered@example.com")
            self.assertEqual(identity["locations"][0]["folder"], "Inbox")
            self.assertEqual(identity["workspaces"], [])
            self.assertIsNone(identity["metadata"])

    def test_salted_id_is_stable_and_index_is_bidirectional(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            salt = get_or_create_salt(root / "salt")
            pipeline_id = make_pipeline_id(salt, "<message@example.com>")
            self.assertEqual(pipeline_id, make_pipeline_id(salt, "<message@example.com>"))
            self.assertNotEqual(pipeline_id, make_pipeline_id(salt, "<other@example.com>"))

            index = MailIdentityIndex(root / "index.sqlite3")
            index.record(
                pipeline_id=pipeline_id,
                rfc_message_id="<message@example.com>",
                identity_source="rfc_message_id",
                account="outlook",
                folder="Inbox",
                himalaya_id="42",
                observed_date="2026-09-28",
                sent_at="2026-09-28T00:00:00Z",
                subject="Initial subject",
                sender="Sender <sender@example.com>",
            )
            index.record_imap_location(
                pipeline_id=pipeline_id,
                account="outlook",
                folder="Inbox",
                uidvalidity=123,
                uid=42,
                observed_date="2026-09-28",
            )
            workspace = root / "2026-09-28" / "emails" / pipeline_id
            index.record_workspace(pipeline_id, "2026-09-28", workspace)
            by_pipeline = index.lookup_pipeline_id(pipeline_id)
            by_rfc = index.lookup_rfc_message_id("<message@example.com>")

            self.assertEqual(by_pipeline["rfc_message_id"], "message@example.com")
            self.assertEqual(by_pipeline["locations"][0]["imap_uid"], 42)
            self.assertEqual(by_pipeline["locations"][0]["uidvalidity"], 123)
            self.assertEqual(by_pipeline["workspaces"][0]["path"], str(workspace))
            self.assertFalse(by_pipeline["summarized"])
            self.assertEqual(by_pipeline["metadata"]["subject"], "Initial subject")
            self.assertEqual(by_pipeline["metadata"]["sender"], "Sender <sender@example.com>")
            index.update_metadata(
                pipeline_id,
                recipients="Recipient <recipient@example.com>",
                cc="Copy <copy@example.com>",
            )
            updated = index.lookup_pipeline_id(pipeline_id)
            self.assertEqual(updated["metadata"]["recipients"], "Recipient <recipient@example.com>")
            self.assertEqual(updated["metadata"]["cc"], "Copy <copy@example.com>")
            self.assertEqual(by_rfc[0]["pipeline_id"], pipeline_id)
            index.set_summarized(pipeline_id)
            summarized = index.lookup_pipeline_id(pipeline_id)
            self.assertTrue(summarized["summarized"])
            self.assertIsNotNone(summarized["summarized_at"])


if __name__ == "__main__":
    unittest.main()
