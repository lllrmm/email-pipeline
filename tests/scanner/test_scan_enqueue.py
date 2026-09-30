from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex
from email_pipeline.scanner.scan import enqueue_scan_result


class ScanEnqueueTests(unittest.TestCase):
    def test_enqueue_scan_result_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config_path = root / "pipeline.toml"
            config_path.write_text(
                "timezone = \"UTC\"\n"
                "[identity]\n"
                f"database_path = \"{root / 'mail-index.sqlite3'}\"\n",
                encoding="utf-8",
            )
            result = {
                "date": "2026-10-01",
                "mails": [
                    {
                        "rfc_message_id": "<a@example.com>",
                        "folder": "INBOX",
                        "uidvalidity": 1,
                        "uid": 11,
                        "received_at": "2026-10-01T00:00:00Z",
                    },
                    {
                        "rfc_message_id": "<b@example.com>",
                        "folder": "INBOX",
                        "uidvalidity": 1,
                        "uid": 12,
                        "received_at": "2026-10-01T00:01:00Z",
                    },
                ],
            }

            first = enqueue_scan_result(result, config_path)
            second = enqueue_scan_result(result, config_path)

            self.assertEqual((first["queued"], first["skipped"]), (2, 0))
            self.assertEqual((second["queued"], second["skipped"]), (0, 2))
            index = MailIdentityIndex(root / "mail-index.sqlite3")
            self.assertEqual(len(index.list_queue_events()), 2)


if __name__ == "__main__":
    unittest.main()
