from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from email_pipeline.mail_identity import MailIdentityIndex


ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StageBoundaryTests(unittest.TestCase):
    def test_unpack_stage_has_no_model_dependency(self) -> None:
        source = (ROOT / "scan_mails.py").read_text(encoding="utf-8")
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

    def test_incremental_skip_logic_belongs_to_scanner(self) -> None:
        scanner = (ROOT / "scan_mails.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        indexer = (ROOT / "index_mail.py").read_text(encoding="utf-8")
        summarizer = (ROOT / "summarize-mail-agentic.py").read_text(encoding="utf-8")

        self.assertIn("classify_summary_state", scanner)
        self.assertIn('identity.get("summarized") is not True', scanner)
        self.assertNotIn('identity.get("summarized")', orchestrator)
        self.assertNotIn('identity.get("summarized")', indexer)
        self.assertNotIn('identity.get("summarized")', summarizer)

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
        source = (ROOT / "aggregate-mails-agentic.py").read_text(encoding="utf-8")

        self.assertNotIn('workdir / "pipeline-id-list.json"', source)
        self.assertIn("INPUT_PIPELINE_IDS_JSON", source)

    def test_aggregation_outputs_are_timestamped_in_aggregation_directory(self) -> None:
        aggregator = (ROOT / "aggregate-mails-agentic.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--output-dir"', aggregator)
        self.assertNotIn('parser.add_argument("--output"', aggregator)
        self.assertIn('workdir / "aggregation"', aggregator)
        self.assertIn('return f"aggregation-', aggregator)
        self.assertIn('day_dir / "aggregation"', orchestrator)

    def test_scan_log_replaces_mail_index_and_bundle_artifacts(self) -> None:
        indexer = (ROOT / "scan_mails.py").read_text(encoding="utf-8")
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

    def test_scanner_reuses_database_summarized_messages(self) -> None:
        scanner = load_script("scan_mails_incremental", ROOT / "scan_mails.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            day_dir = Path(temp_dir) / "2026-09-28"
            pipeline_id = "a" * 64
            mail_dir = day_dir / "emails" / pipeline_id
            mail_dir.mkdir(parents=True)
            database = Path(temp_dir) / "mail-index.sqlite3"
            index = MailIdentityIndex(database)
            index.record(
                pipeline_id=pipeline_id,
                rfc_message_id="<incremental@example.com>",
                identity_source="rfc_message_id",
                account="outlook",
                folder="Inbox",
                himalaya_id="42",
                observed_date="2026-09-28",
            )
            (mail_dir / "request.json").write_text(json.dumps({
                "pipeline_id": pipeline_id,
                "index_database": str(database),
            }), encoding="utf-8")
            (mail_dir / "summary.json").write_text(json.dumps({
                "pipeline_id": pipeline_id,
                "analysis": {"summary": "done"},
            }), encoding="utf-8")
            index.set_summarized(pipeline_id)

            state = scanner.classify_summary_state(database, day_dir, pipeline_id)

            self.assertEqual(state, "reused")

    def test_summarized_database_state_requires_summary_artifact(self) -> None:
        scanner = load_script("scan_mails_consistency", ROOT / "scan_mails.py")
        with tempfile.TemporaryDirectory() as temp_dir:
            day_dir = Path(temp_dir) / "2026-09-28"
            pipeline_id = "b" * 64
            mail_dir = day_dir / "emails" / pipeline_id
            mail_dir.mkdir(parents=True)
            database = Path(temp_dir) / "mail-index.sqlite3"
            index = MailIdentityIndex(database)
            index.record(
                pipeline_id=pipeline_id,
                rfc_message_id="<missing@example.com>",
                identity_source="rfc_message_id",
                account="outlook",
                folder="Inbox",
                himalaya_id="43",
                observed_date="2026-09-28",
            )
            (mail_dir / "request.json").write_text(json.dumps({
                "pipeline_id": pipeline_id,
                "index_database": str(database),
            }), encoding="utf-8")
            index.set_summarized(pipeline_id)

            with self.assertRaisesRegex(RuntimeError, "summary.json is missing"):
                scanner.classify_summary_state(database, day_dir, pipeline_id)

    def test_orchestrator_consumes_scan_log_summary_partition(self) -> None:
        orchestrator = load_script("daily_orchestrator_partition", ROOT / "daily-mail-pipeline.py")
        included = ["a" * 64, "b" * 64]
        scan_log = {
            "included_pipeline_ids": included,
            "pending_summary_pipeline_ids": [included[1]],
            "reused_summary_pipeline_ids": [included[0]],
        }

        actual_included, pending, reused = orchestrator.summary_partition_from_scan_log(scan_log)

        self.assertEqual(actual_included, included)
        self.assertEqual(pending, [included[1]])
        self.assertEqual(reused, [included[0]])


if __name__ == "__main__":
    unittest.main()
