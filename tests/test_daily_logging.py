from __future__ import annotations

import contextlib
import datetime as dt
import io
import logging
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from email_pipeline.daily_logging import configure_daily_logger, configure_run_logger, parse_log_level


class ParseLogLevelTests(unittest.TestCase):
    def test_known_levels_are_case_insensitive(self) -> None:
        self.assertEqual(parse_log_level("debug"), logging.DEBUG)
        self.assertEqual(parse_log_level("WARN"), logging.WARNING)
        self.assertEqual(parse_log_level("Error"), logging.ERROR)

    def test_missing_level_defaults_to_info(self) -> None:
        self.assertEqual(parse_log_level(None), logging.INFO)
        self.assertEqual(parse_log_level(""), logging.INFO)

    def test_invalid_level_warns_and_falls_back(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(parse_log_level("bogus"), logging.INFO)
        self.assertIn("invalid log level", stderr.getvalue())

    def test_default_is_used_when_missing_or_invalid(self) -> None:
        self.assertEqual(parse_log_level(None, default=logging.WARNING), logging.WARNING)
        self.assertEqual(parse_log_level("", default=logging.ERROR), logging.ERROR)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertEqual(parse_log_level("bogus", default=logging.DEBUG), logging.DEBUG)
        self.assertIn("invalid log level", stderr.getvalue())


class DailyLoggerLevelTests(unittest.TestCase):
    def test_logger_respects_configured_level(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            logger = configure_daily_logger(output_root, "testcmp", "UTC", file_level=logging.WARNING)
            logger.info("hidden")
            logger.warning("visible")
            day = dt.datetime.now(ZoneInfo("UTC")).date().isoformat()
            log_path = output_root / day / "logs" / "testcmp" / "testcmp.log"
            content = log_path.read_text(encoding="utf-8")
            self.assertIn("visible", content)
            self.assertNotIn("hidden", content)

    def test_console_level_is_independent_from_file_level(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                logger = configure_daily_logger(output_root, "testconsole", "UTC", file_level=logging.INFO, console_level=logging.WARNING)
                logger.info("file-only")
                logger.warning("both")
                logger.error("console-err")
            day = dt.datetime.now(ZoneInfo("UTC")).date().isoformat()
            content = (output_root / day / "logs" / "testconsole" / "testconsole.log").read_text(encoding="utf-8")
            self.assertIn("file-only", content)
            self.assertIn("both", content)
            self.assertIn("console-err", content)
            self.assertNotIn("file-only", stdout.getvalue())
            self.assertIn("both", stdout.getvalue())
            self.assertIn("console-err", stderr.getvalue())

    def test_run_logger_console_error_threshold_only_writes_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                logger = configure_run_logger(output_root, "testrun", "UTC", file_level=logging.INFO, console_level=logging.ERROR)
                logger.info("file-only")
                logger.error("console-err")
            day = dt.datetime.now(ZoneInfo("UTC")).date().isoformat()
            log_dir = output_root / day / "logs" / "testrun"
            log_files = list(log_dir.glob("testrun-*.log"))
            self.assertEqual(len(log_files), 1)
            self.assertIn("file-only", log_files[0].read_text(encoding="utf-8"))
            self.assertEqual(stdout.getvalue(), "")
            self.assertIn("console-err", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
