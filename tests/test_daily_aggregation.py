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
    def test_valid_agent_output_is_preserved_verbatim(self) -> None:
        result = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [{
                "kind": "scheduled",
                "title": "Event",
                "start": "2026-10-01T10:00:00+08:00",
                "end": None,
                "timezone": "Asia/Hong_Kong",
                "location": "HKUST",
                "due": None,
                "priority": "high",
                "confidence": "high",
                "source_messages": [{
                    "folder": "Inbox", "id": "42", "subject": "Subject"
                }],
            }],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }

        MODULE.validate_daily_summary(result)
        self.assertEqual(result["events"][0]["title"], "Event")

    def test_old_or_extra_fields_are_rejected_not_rewritten(self) -> None:
        invalid = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [],
            "deadlines": [],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }
        with self.assertRaises(ValueError):
            MODULE.validate_daily_summary(invalid)


if __name__ == "__main__":
    unittest.main()
