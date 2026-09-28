from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageBoundaryTests(unittest.TestCase):
    def test_unpack_stage_has_no_model_dependency(self) -> None:
        source = (ROOT / "unpack-mail.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("DEEPSEEK_API_KEY", source)
        self.assertNotIn("call_model(", source)

    def test_summary_stage_has_no_mailbox_dependency(self) -> None:
        source = (ROOT / "summarize-mail.py").read_text(encoding="utf-8")
        self.assertNotIn("himalaya", source.lower())
        self.assertNotIn("message read", source.lower())

    def test_orchestrator_has_no_provider_or_parser_dependency(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("import yaml", source)
        self.assertNotIn("himalaya", source.lower())
        self.assertNotIn("extract_message", source)

    def test_image_only_summary_is_deterministic_without_model_call(self) -> None:
        summary_module = load_script("summary_stage", ROOT / "summarize-mail.py")
        result = summary_module.summarize_message(
            {"image_only": True, "raw_path": None, "attachment_text_path": None},
            {},
            "unused",
        )
        self.assertEqual(result["error"], "image_only_no_ocr")

    def test_compatibility_bundle_joins_matching_artifacts(self) -> None:
        orchestrator = load_script("daily_orchestrator", ROOT / "daily-mail-pipeline.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unpack_path = root / "unpack.json"
            summary_path = root / "summary.json"
            unpack = {
                "schema_version": 2,
                "artifact_type": "mail_unpack",
                "date": "2026-09-25",
                "messages_total": 1,
                "messages": [{
                    "folder": "Inbox",
                    "id": "42",
                    "message_id": "message@example",
                    "subject": "Test",
                    "raw_path": str(root / "raw.txt"),
                }],
            }
            unpack_bytes = json.dumps(unpack).encode("utf-8")
            unpack_path.write_bytes(unpack_bytes)
            summary = {
                "artifact_type": "mail_daily_summary",
                "processor": {"processor": "opencode", "session_id": "ses-test"},
                "daily_summary": {
                    "date": "2026-09-25",
                    "overview": "Test summary",
                    "urgent_items": [],
                    "events": [],
                    "deadlines": [],
                    "actions": [],
                    "warnings": [],
                    "messages_total": 1,
                    "messages_requiring_review": 0,
                },
            }
            summary_path.write_text(json.dumps(summary), encoding="utf-8")

            bundle_path, bundle, daily_summary = orchestrator.materialize_bundle(unpack_path, summary_path)

            self.assertEqual(bundle["artifact_type"], "mail_digest")
            self.assertEqual(daily_summary["overview"], "Test summary")
            self.assertEqual(bundle["message_index"][0]["subject"], "Test")
            forbidden = {
                "size", "raw_path", "eml_path", "attachment_manifest_path",
                "attachment_text_path", "body_original_chars", "body_extracted_chars",
                "links", "images", "attachments",
            }
            encoded = json.dumps(bundle)
            for key in forbidden:
                self.assertNotIn(f'"{key}"', encoded)
            self.assertTrue(bundle_path.exists())


if __name__ == "__main__":
    unittest.main()
