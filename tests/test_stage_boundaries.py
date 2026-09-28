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
        source = (ROOT / "index-mail.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("DEEPSEEK_API_KEY", source)
        self.assertNotIn("call_model(", source)

    def test_initiator_calls_agentic_summary_by_pipeline_id(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertIn("summarize-mail-agentic.py", source)
        self.assertIn('"--pipeline-id"', source)
        self.assertIn('"--agent-workdir"', source)
        self.assertIn('"--output"', source)

    def test_orchestrator_has_no_provider_or_parser_dependency(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("himalaya", source.lower())
        self.assertNotIn("extract_message", source)

    def test_removed_intermediate_summary_script_stays_removed(self) -> None:
        self.assertFalse((ROOT / "summarize-mail.py").exists())

    def test_compatibility_bundle_joins_matching_artifacts(self) -> None:
        orchestrator = load_script("daily_orchestrator", ROOT / "daily-mail-pipeline.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            unpack_path = root / "mail-index.json"
            summary_path = root / "summary.json"
            unpack = {
                "schema_version": 2,
                "artifact_type": "mail_index",
                "date": "2026-09-25",
                "messages_total": 1,
                "messages": [{
                    "pipeline_id": "abc123",
                    "subject": "Test",
                    "date": "2026-09-25T10:00:00+08:00",
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
                    "events": [],
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
