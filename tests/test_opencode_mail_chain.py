from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

from email_pipeline import agent_tools


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("opencode_mail", ROOT / "summarize-mail-agentic.py")
assert SPEC and SPEC.loader
OPENCODE_MAIL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OPENCODE_MAIL)


class OpenCodeMailChainTests(unittest.TestCase):
    def test_lossless_unpack_and_agent_requested_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            (workspace / "request.json").write_text(
                json.dumps({"pipeline_id": "abc123", "index_database": str(workspace / "index.sqlite3")}), encoding="utf-8"
            )
            message = EmailMessage()
            message["Subject"] = "Linked event"
            message.set_content("Fallback")
            message.add_alternative(
                '<html><body><a href="https://example.org/event">Event page</a></body></html>',
                subtype="html",
            )
            message.add_attachment(
                b"Deadline,2026-10-08\n",
                maintype="text",
                subtype="csv",
                filename="dates.csv",
            )
            (workspace / "message.eml").write_bytes(message.as_bytes())

            unpacked = agent_tools.command_unpack(workspace)
            attachment = unpacked["attachments"][0]
            html_part = next(item for item in unpacked["parts"] if item["content_type"] == "text/html")
            extracted = agent_tools.command_extract_attachment(workspace, attachment["id"])
            links = agent_tools.command_extract_links(workspace, html_part["id"])

            self.assertEqual(extracted["status"], "extracted")
            self.assertIn("2026-10-08", extracted["excerpt"])
            self.assertEqual(links["links"][0]["url"], "https://example.org/event")

    def test_event_parser_collects_session_tools_and_final_json(self) -> None:
        stdout = "\n".join([
            json.dumps({"type": "session", "sessionID": "ses-123"}),
            json.dumps({"type": "tool_call", "tool": "mail_fetch"}),
            json.dumps({"type": "tool_call", "tool": "mail_unpack"}),
            json.dumps({"type": "text", "text": json.dumps({"summary": "done", "events": [], "deadlines": []})}),
        ])

        session_id, tools, result = OPENCODE_MAIL.parse_events(stdout)

        self.assertEqual(session_id, "ses-123")
        self.assertEqual(tools, ["mail_fetch", "mail_unpack"])
        self.assertEqual(result["summary"], "done")

    def test_daily_validator_returns_errors_without_modifying(self) -> None:
        candidate = {
            "date": "2026-09-25",
            "overview": "overview",
            "events": [],
            "warnings": [],
            "messages_total": 1,
            "messages_requiring_review": 0,
        }
        valid = agent_tools.command_validate_daily_summary(json.dumps(candidate))
        invalid = agent_tools.command_validate_daily_summary(json.dumps({**candidate, "deadlines": []}))

        self.assertTrue(valid["valid"])
        self.assertFalse(invalid["valid"])
        self.assertIn("daily summary keys", invalid["errors"][0])


if __name__ == "__main__":
    unittest.main()
