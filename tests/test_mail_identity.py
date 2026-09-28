from __future__ import annotations

import tempfile
import unittest
import importlib.util
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex, get_or_create_salt, make_pipeline_id

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("index_mail", ROOT / "index-mail.py")
assert SPEC and SPEC.loader
INDEX_MAIL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INDEX_MAIL)


class MailIdentityTests(unittest.TestCase):
    def test_missing_rfc_message_id_is_a_hard_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            with self.assertRaisesRegex(RuntimeError, "RFC Message-ID missing"):
                INDEX_MAIL.process_message(
                    "Inbox",
                    {"id": "42", "subject": "No id", "date": "2026-09-28T00:00:00Z"},
                    root,
                    account="outlook",
                    salt=b"x" * 32,
                    index_path=root / "index.sqlite3",
                )

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
            )
            workspace = root / "2026-09-28" / "emails" / pipeline_id
            index.record_workspace(pipeline_id, "2026-09-28", workspace)
            by_pipeline = index.lookup_pipeline_id(pipeline_id)
            by_rfc = index.lookup_rfc_message_id("<message@example.com>")

            self.assertEqual(by_pipeline["rfc_message_id"], "<message@example.com>")
            self.assertEqual(by_pipeline["locations"][0]["himalaya_id"], "42")
            self.assertEqual(by_pipeline["workspaces"][0]["path"], str(workspace))
            self.assertFalse(by_pipeline["summarized"])
            self.assertEqual(by_rfc[0]["pipeline_id"], pipeline_id)
            index.set_summarized(pipeline_id)
            summarized = index.lookup_pipeline_id(pipeline_id)
            self.assertTrue(summarized["summarized"])
            self.assertIsNotNone(summarized["summarized_at"])


if __name__ == "__main__":
    unittest.main()
