from __future__ import annotations

import datetime as dt
import logging
import unittest
from zoneinfo import ZoneInfo

from email_pipeline.daily_logging import ZonedFormatter
from email_pipeline.program_time import configure_program_timezone, format_rfc3339


class ProgramTimeTests(unittest.TestCase):
    def test_rfc3339_output_uses_configured_offset(self) -> None:
        configure_program_timezone("Asia/Hong_Kong")
        value = format_rfc3339(dt.datetime(2026, 9, 28, 10, 0, tzinfo=dt.timezone.utc))
        self.assertEqual(value, "2026-09-28T18:00:00+08:00")

    def test_log_timestamp_has_rfc3339_separator_and_offset(self) -> None:
        record = logging.LogRecord("test", logging.INFO, "", 0, "message", (), None)
        record.created = dt.datetime(2026, 9, 28, 10, 0, tzinfo=dt.timezone.utc).timestamp()
        value = ZonedFormatter(ZoneInfo("Asia/Hong_Kong")).formatTime(record)
        self.assertEqual(value, "2026-09-28T18:00:00+08:00")


if __name__ == "__main__":
    unittest.main()
