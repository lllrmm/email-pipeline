from __future__ import annotations

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
        self.assertIn('"--mail-dir"', source)
        self.assertIn('"--output"', source)
        self.assertIn('get("concurrency") or 8', source)

    def test_agentic_output_path_is_caller_selected_inside_workspace(self) -> None:
        source = (ROOT / "summarize-mail-agentic.py").read_text(encoding="utf-8")
        self.assertNotIn("output_path.name", source)
        self.assertIn("workspace not in output_path.parents", source)
        self.assertIn('runtime_root / "sessions" / pipeline_id', source)

    def test_orchestrator_has_no_provider_or_parser_dependency(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("himalaya", source.lower())
        self.assertNotIn("extract_message", source)

    def test_removed_intermediate_summary_script_stays_removed(self) -> None:
        self.assertFalse((ROOT / "summarize-mail.py").exists())

    def test_aggregation_does_not_materialize_pipeline_id_list(self) -> None:
        source = (ROOT / "mails-aggregate-agentic.py").read_text(encoding="utf-8")

        self.assertNotIn('workdir / "pipeline-id-list.json"', source)
        self.assertIn("INPUT_PIPELINE_IDS_JSON", source)

    def test_aggregation_outputs_are_timestamped_in_aggregation_directory(self) -> None:
        aggregator = (ROOT / "mails-aggregate-agentic.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--output-dir"', aggregator)
        self.assertNotIn('parser.add_argument("--output"', aggregator)
        self.assertIn('workdir / "aggregation"', aggregator)
        self.assertIn('return f"aggregation-', aggregator)
        self.assertIn('day_dir / "aggregation"', orchestrator)

    def test_scan_log_replaces_mail_index_and_bundle_artifacts(self) -> None:
        indexer = (ROOT / "index-mail.py").read_text(encoding="utf-8")
        orchestrator_source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")

        self.assertIn('artifact_type": "mail_scan_log"', indexer)
        self.assertIn('/ "scan-log"', indexer)
        self.assertNotIn('"mail-index.json"', indexer)
        self.assertNotIn('"mail-index.json"', orchestrator_source)
        self.assertNotIn('"bundle.json"', orchestrator_source)
        self.assertNotIn("materialize_bundle", orchestrator_source)

    def test_orchestrator_reads_daily_summary_directly_from_aggregation(self) -> None:
        orchestrator = load_script("daily_orchestrator", ROOT / "daily-mail-pipeline.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            aggregation_path = root / "aggregation.json"
            scan_log = {
                "schema_version": 1,
                "artifact_type": "mail_scan_log",
                "date": "2026-09-25",
                "messages_total": 1,
                "included_pipeline_ids": ["abc123"],
            }
            aggregation = {
                "artifact_type": "mail_daily_summary",
                "daily_summary": {
                    "date": "2026-09-25",
                    "overview": "Test summary",
                    "events": [],
                    "warnings": [],
                    "messages_total": 1,
                    "messages_requiring_review": 0,
                },
            }
            aggregation_path.write_text(json.dumps(aggregation), encoding="utf-8")

            daily_summary = orchestrator.load_daily_summary(scan_log, aggregation_path)

            self.assertEqual(daily_summary["overview"], "Test summary")


if __name__ == "__main__":
    unittest.main()
