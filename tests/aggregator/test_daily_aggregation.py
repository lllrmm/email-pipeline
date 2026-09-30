from __future__ import annotations

import importlib.util
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex
from email_pipeline.program_time import configure_program_timezone


from email_pipeline.aggregator import aggregate as MODULE

class DailyAggregationTests(unittest.TestCase):
    def test_aggregation_artifact_records_every_input_pipeline_id(self) -> None:
        pipeline_ids = ["a" * 64, "b" * 64]
        daily_summary = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [],
            "warnings": [],
            "messages_total": 2,
            "messages_requiring_review": 0,
        }

        artifact = MODULE.build_aggregation_artifact(
            daily_summary,
            pipeline_ids,
            earliest_email_at="2026-09-24T20:00:00Z",
            latest_email_at="2026-09-25T12:00:00Z",
            session_id="session-1",
            model="test-model",
            tools_used=["mail_validate_daily_summary"],
        )

        self.assertEqual(artifact["included_pipeline_ids"], pipeline_ids)
        self.assertEqual(artifact["earliest_email_at"], "2026-09-24T20:00:00Z")
        self.assertEqual(artifact["latest_email_at"], "2026-09-25T12:00:00Z")
        self.assertIs(artifact["daily_summary"], daily_summary)
        self.assertNotIn("included_pipeline_ids", daily_summary)

    def test_email_time_bounds_compare_actual_instants(self) -> None:
        configure_program_timezone("Asia/Hong_Kong")
        earliest, latest = MODULE.email_time_bounds([
            "2026-09-25T12:00:00+08:00",
            "2026-09-25T05:00:00Z",
            "not-a-date",
        ])

        self.assertEqual(earliest, "2026-09-25T12:00:00+08:00")
        self.assertEqual(latest, "2026-09-25T13:00:00+08:00")

    def test_aggregation_timestamp_uses_configured_timezone(self) -> None:
        configure_program_timezone("Asia/Hong_Kong")
        generated_at = MODULE.generated_at()

        self.assertTrue(generated_at.endswith("+08:00"))
        self.assertNotIn(".", generated_at)
        parsed = dt.datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        self.assertEqual(parsed.utcoffset(), dt.timedelta(hours=8))

    def test_aggregation_filename_uses_configured_timezone(self) -> None:
        configure_program_timezone("Asia/Hong_Kong")
        filename = MODULE.aggregation_filename("2026-09-28T10:04:35.098404Z")

        self.assertEqual(filename, "aggregation-20260928T180435+0800.json")

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
            index.register_identity(
                pipeline_id=pipeline_id,
                rfc_message_id="<test@example.com>",
            )
            index.record_imap_location(pipeline_id=pipeline_id, account="outlook", folder="Inbox", uidvalidity=123, uid=42)
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
