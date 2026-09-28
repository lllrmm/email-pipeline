from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("daily_aggregator", ROOT / "opencode-daily-summary.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DailyAggregationTests(unittest.TestCase):
    def test_output_is_strictly_whitelisted(self) -> None:
        result = MODULE.normalize_daily_summary({
            "date": "2026-09-25",
            "overview": "overview",
            "links": [{"url": "https://secret.example"}],
            "raw_path": "/private/mail.txt",
            "events": [{
                "title": "Event",
                "start": "2026-10-01T10:00:00+08:00",
                "end": None,
                "timezone": "Asia/Hong_Kong",
                "location": "HKUST",
                "confidence": "high",
                "attachments": ["private.pdf"],
                "source_messages": [{
                    "folder": "Inbox", "id": "42", "subject": "Subject",
                    "raw_path": "/private/mail.txt",
                }],
            }],
            "deadlines": [],
            "actions": [],
            "urgent_items": [],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        })

        self.assertNotIn("links", result)
        self.assertNotIn("raw_path", result)
        self.assertNotIn("attachments", result["events"][0])
        self.assertEqual(
            result["events"][0]["source_messages"][0],
            {"folder": "Inbox", "id": "42", "subject": "Subject"},
        )


if __name__ == "__main__":
    unittest.main()
