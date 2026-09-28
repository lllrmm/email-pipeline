from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("daily_aggregator", ROOT / "mails-aggregate-agentic.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class DailyAggregationTests(unittest.TestCase):
    def test_parser_extracts_json_from_surrounding_text(self) -> None:
        value = MODULE.parse_json_object('Result follows:\n```json\n{"overview":"ok"}\n```')
        self.assertEqual(value, {"overview": "ok"})

    def test_valid_agent_output_is_preserved_verbatim(self) -> None:
        result = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [{
                "title": "Event",
                "scheduled": [{
                    "content": "Attend the event",
                    "start": "2026-10-01T10:00:00+08:00",
                    "end": None,
                    "timezone": "Asia/Hong_Kong",
                    "location": "HKUST",
                    "confidence": "high",
                }],
                "deadlines": [{
                    "title": "Registration deadline",
                    "content": "Complete registration before the deadline",
                    "due": "2026-09-30T23:59:00+08:00",
                    "timezone": "Asia/Hong_Kong",
                    "priority": "high",
                    "confidence": "high",
                }],
                "actions": [{
                    "title": "Register",
                    "content": "Submit the event registration form",
                    "due": "2026-09-30T23:59:00+08:00",
                    "priority": "high",
                    "confidence": "high",
                }],
                "priority": "high",
                "confidence": "high",
                "source_messages": [{
                    "pipeline_id": "a" * 64, "subject": "Subject"
                }],
                "warnings": [],
            }],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }

        MODULE.validate_daily_summary(result)
        self.assertEqual(result["events"][0]["title"], "Event")

    def test_time_only_nested_item_is_rejected(self) -> None:
        result = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [{
                "title": "Event",
                "scheduled": [{
                    "content": "",
                    "start": "2026-10-01T10:00:00+08:00",
                    "end": None,
                    "timezone": "Asia/Hong_Kong",
                    "location": "HKUST",
                    "confidence": "high",
                }],
                "deadlines": [],
                "actions": [],
                "priority": "high",
                "confidence": "high",
                "source_messages": [],
                "warnings": [],
            }],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }

        with self.assertRaisesRegex(ValueError, "content must be a non-empty string"):
            MODULE.validate_daily_summary(result)

    def test_old_or_extra_fields_are_rejected_not_rewritten(self) -> None:
        invalid = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [{
                "kind": "scheduled",
                "title": "Old flat item",
                "start": None,
                "end": None,
                "due": None,
                "timezone": None,
                "location": None,
                "priority": "low",
                "confidence": "high",
                "source_messages": [],
            }],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }
        with self.assertRaises(ValueError):
            MODULE.validate_daily_summary(invalid)

    def test_aggregation_requires_every_pipeline_id_to_be_summarized(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workdir = Path(temp_dir) / "2026-09-28"
            pipeline_id = "a" * 64
            email_dir = workdir / "emails" / pipeline_id
            email_dir.mkdir(parents=True)
            database = Path(temp_dir) / "mail-index.sqlite3"
            index = MailIdentityIndex(database)
            index.record(
                pipeline_id=pipeline_id,
                rfc_message_id="<test@example.com>",
                identity_source="rfc_message_id",
                account="outlook",
                folder="Inbox",
                himalaya_id="42",
                observed_date="2026-09-28",
            )
            (email_dir / "request.json").write_text(json.dumps({
                "pipeline_id": pipeline_id,
                "index_database": str(database),
            }), encoding="utf-8")
            (email_dir / "summary.json").write_text(json.dumps({
                "pipeline_id": pipeline_id,
                "analysis": {"summary": "done"},
            }), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "not summarized"):
                MODULE.validate_pipeline_inputs(workdir, [pipeline_id])

            index.set_summarized(pipeline_id)
            MODULE.validate_pipeline_inputs(workdir, [pipeline_id])


if __name__ == "__main__":
    unittest.main()
