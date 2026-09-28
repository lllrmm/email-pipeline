from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "set_email_summary_key.py"
SPEC = importlib.util.spec_from_file_location("set_email_summary_key", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class EmailSummaryKeyTests(unittest.TestCase):
    def test_key_is_replaced_without_touching_other_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            config = root / "pipeline.toml"
            env = root / ".env"
            config.write_text(
                "[summarizer.api]\n"
                "api_key = \"old-inline\"\n"
                "api_key_env = \"DEEPSEEK_API_KEY\"\n"
                "[aggregator.api]\n"
                "api_key = \"old-inline\"\n"
                "api_key_env = \"DEEPSEEK_API_KEY\"\n",
                encoding="utf-8",
            )
            env.write_text(
                "DEEPSEEK_API_KEY=general-key\n"
                "EMAIL_SUMMARY_DEEPSEEK_API_KEY=old-summary-key\n",
                encoding="utf-8",
            )

            MODULE.configure_key(config, env, "EMAIL_SUMMARY_DEEPSEEK_API_KEY", "new-summary-key")

            config_text = config.read_text(encoding="utf-8")
            env_text = env.read_text(encoding="utf-8")
            self.assertEqual(config_text.count('api_key = ""'), 2)
            self.assertEqual(config_text.count('api_key_env = "EMAIL_SUMMARY_DEEPSEEK_API_KEY"'), 2)
            self.assertIn("DEEPSEEK_API_KEY=general-key", env_text)
            self.assertEqual(env_text.count("EMAIL_SUMMARY_DEEPSEEK_API_KEY=new-summary-key"), 1)
            self.assertNotIn("old-summary-key", env_text)


if __name__ == "__main__":
    unittest.main()
