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

    def test_scan_orchestrator_only_enqueues(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertIn("enqueue_event", source)
        self.assertNotIn("summarize-mail-agentic.py", source)
        self.assertNotIn("aggregate-mails-agentic.py", source)
        self.assertNotIn("index_mail.py", source)
        self.assertNotIn("summarized", source)

    def test_scanner_and_fallback_use_queue_as_idempotency_boundary(self) -> None:
        scanner = (ROOT / "scan_mails.py").read_text(encoding="utf-8")
        orchestrator = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        indexer = (ROOT / "index_mail.py").read_text(encoding="utf-8")
        summarizer = (ROOT / "summarize-mail-agentic.py").read_text(encoding="utf-8")

        self.assertNotIn('identity.get("summarized")', scanner)
        self.assertIn("enqueue_event", orchestrator)
        self.assertNotIn('identity.get("summarized")', orchestrator)
        self.assertNotIn('identity.get("summarized")', indexer)
        self.assertNotIn('identity.get("summarized")', summarizer)

    def test_index_mail_accepts_only_rfc_identity_inputs(self) -> None:
        indexer = (ROOT / "index_mail.py").read_text(encoding="utf-8")
        scanner = (ROOT / "scan_mails.py").read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--rfc-message-id"', indexer)
        self.assertIn('parser.add_argument("--folder"', indexer)
        self.assertIn('parser.add_argument("--uidvalidity"', indexer)
        self.assertIn('parser.add_argument("--uid"', indexer)
        self.assertNotIn('parser.add_argument("--envelope-json"', indexer)
        self.assertIn("record_imap_location", indexer)
        self.assertNotIn("record_workspace", indexer)
        self.assertNotIn("update_metadata", indexer)

    def test_agentic_output_path_is_caller_selected_inside_workspace(self) -> None:
        source = (ROOT / "summarize-mail-agentic.py").read_text(encoding="utf-8")
        self.assertNotIn("output_path.name", source)
        self.assertIn("workspace not in output_path.parents", source)
        self.assertIn('runtime_root / "sessions" / pipeline_id', source)
        self.assertIn('workspace / "message.eml"', source)
        self.assertIn('workspace / "manifest.json"', source)

    def test_orchestrator_has_no_provider_or_parser_dependency(self) -> None:
        source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")
        self.assertNotIn("import requests", source)
        self.assertNotIn("himalaya envelope", source.lower())
        self.assertNotIn("extract_message", source)

    def test_removed_intermediate_summary_script_stays_removed(self) -> None:
        self.assertFalse((ROOT / "summarize-mail.py").exists())

    def test_event_watcher_enqueues_and_consumer_summarizes_without_aggregation(self) -> None:
        watcher = (ROOT / "watch_mails.py").read_text(encoding="utf-8")
        consumer = (ROOT / "consume_mail_queue.py").read_text(encoding="utf-8")
        self.assertIn("enqueue_event", watcher)
        self.assertNotIn("daily-mail-pipeline.py", watcher)
        self.assertIn("summarize-mail-agentic.py", consumer)
        self.assertNotIn("aggregate-mails-agentic.py", consumer)

    def test_daily_aggregation_waits_for_queue_completion(self) -> None:
        source = (ROOT / "get_daily_aggregation.py").read_text(encoding="utf-8")
        self.assertIn("wait_for_done", source)
        self.assertIn('event.get("status") != "done"', source)
        self.assertIn("aggregate-mails-agentic.py", source)

    def test_aggregation_does_not_materialize_pipeline_id_list(self) -> None:
        source = (ROOT / "aggregate-mails-agentic.py").read_text(encoding="utf-8")

        self.assertNotIn('workdir / "pipeline-id-list.json"', source)
        self.assertIn("INPUT_PIPELINE_IDS_JSON", source)

    def test_aggregation_outputs_are_timestamped_in_aggregation_directory(self) -> None:
        aggregator = (ROOT / "aggregate-mails-agentic.py").read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--output-dir"', aggregator)
        self.assertNotIn('parser.add_argument("--output"', aggregator)
        self.assertIn('workdir / "aggregation"', aggregator)
        self.assertIn('return f"aggregation-', aggregator)
        self.assertNotIn("daily-mail-pipeline.py", aggregator)

    def test_scan_log_replaces_mail_index_and_bundle_artifacts(self) -> None:
        indexer = (ROOT / "scan_mails.py").read_text(encoding="utf-8")
        orchestrator_source = (ROOT / "daily-mail-pipeline.py").read_text(encoding="utf-8")

        self.assertIn('artifact_type": "mail_scan_log"', indexer)
        self.assertIn('/ "scan-log"', indexer)
        self.assertNotIn('"mail-index.json"', indexer)
        self.assertNotIn('"mail-index.json"', orchestrator_source)
        self.assertNotIn('"bundle.json"', orchestrator_source)
        self.assertNotIn("materialize_bundle", orchestrator_source)




if __name__ == "__main__":
    unittest.main()
