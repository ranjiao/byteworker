import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_run_log import (  # noqa: E402
    DigestRunError,
    finish_run,
    list_runs,
    record_stage,
    show_run,
    start_run,
)


class DigestRunLogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.kb = Path(self.temporary.name) / "kb"
        self.kb.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)
        self.started_at = datetime(2026, 8, 21, 1, 2, 3, tzinfo=timezone.utc)
        self.run_id = "DG-20260821T010203Z-1234abcd"

    def tearDown(self):
        self.temporary.cleanup()

    def start(self, **kwargs):
        return start_run(
            self.kb,
            source_type="feishu_doc",
            source_ref="https://example.test/secret?token=credential",
            now=self.started_at,
            run_id=self.run_id,
            **kwargs,
        )

    def test_timeline_computes_stage_and_total_duration_without_source_text(self):
        started = self.start()
        self.assertEqual(self.run_id, started["run_id"])
        self.assertEqual(
            "state/digest/run-logs/2026-08-21.jsonl", started["log_path"]
        )

        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="capture",
            status="started",
            detail_code="FETCH_SOURCE_COMPONENTS",
            now=self.started_at + timedelta(seconds=1),
        )
        completed = record_stage(
            self.kb,
            run_id=self.run_id,
            stage="capture",
            status="completed",
            detail_code="SOURCE_COMPONENTS_READY",
            metrics={"component_count": 3, "input_bytes": 4096},
            now=self.started_at + timedelta(seconds=7, milliseconds=250),
        )
        self.assertEqual(6250, completed["duration_ms"])

        finish_run(
            self.kb,
            run_id=self.run_id,
            status="committed",
            metrics={"node_count": 2, "evidence_count": 8},
            now=self.started_at + timedelta(seconds=10),
        )
        shown = show_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(seconds=20),
        )
        summary = shown["summary"]
        self.assertEqual("committed", summary["status"])
        self.assertEqual(10000, summary["duration_ms"])
        self.assertEqual("capture", summary["slowest_stage"]["stage"])
        self.assertEqual(4, shown["event_count"])

        log_path = self.kb / "state/digest/run-logs/2026-08-21.jsonl"
        log_text = log_path.read_text(encoding="utf-8")
        self.assertNotIn("secret", log_text)
        self.assertNotIn("credential", log_text)
        self.assertNotIn("example.test", log_text)
        self.assertIn("sha256:", log_text)
        self.assertEqual(0o700, os.stat(log_path.parent).st_mode & 0o777)
        self.assertEqual(0o600, os.stat(log_path).st_mode & 0o777)
        self.assertIn(
            "/state/",
            (self.kb / ".git/info/exclude").read_text(encoding="utf-8"),
        )

    def test_list_reports_running_and_terminal_runs(self):
        self.start()
        running = list_runs(
            self.kb, now=self.started_at + timedelta(seconds=4)
        )["runs"][0]
        self.assertEqual("running", running["status"])
        self.assertEqual(4000, running["duration_ms"])

        finish_run(
            self.kb,
            run_id=self.run_id,
            status="noop",
            now=self.started_at + timedelta(seconds=5),
        )
        terminal = list_runs(
            self.kb, now=self.started_at + timedelta(seconds=20)
        )["runs"][0]
        self.assertEqual("noop", terminal["status"])
        self.assertEqual(5000, terminal["duration_ms"])

    def test_source_type_can_be_resolved_after_logging_starts(self):
        start_run(
            self.kb,
            source_type="unknown",
            source_ref="input-1",
            now=self.started_at,
            run_id=self.run_id,
        )
        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="classify",
            status="started",
            now=self.started_at,
        )
        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="classify",
            status="completed",
            source_type="web",
            now=self.started_at + timedelta(seconds=2),
        )
        shown = show_run(self.kb, run_id=self.run_id)
        self.assertEqual("web", shown["summary"]["source_type"])
        self.assertEqual(2000, shown["summary"]["stages"][0]["duration_ms"])

    def test_analysis_prepare_has_its_own_timing_boundary(self):
        self.start()
        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="analysis_prepare",
            status="started",
            now=self.started_at + timedelta(seconds=1),
        )
        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="analysis_prepare",
            status="completed",
            detail_code="ANALYSIS_PACKET_READY",
            metrics={"component_count": 26, "output_count": 1},
            now=self.started_at + timedelta(seconds=2),
        )
        summary = show_run(self.kb, run_id=self.run_id)["summary"]
        self.assertEqual("analysis_prepare", summary["stages"][0]["stage"])
        self.assertEqual(1000, summary["stages"][0]["duration_ms"])

    def test_stage_completion_requires_matching_start(self):
        self.start()
        with self.assertRaisesRegex(DigestRunError, "no open start"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="semantic_analysis",
                status="completed",
                now=self.started_at + timedelta(seconds=1),
            )

        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="semantic_analysis",
            status="started",
            now=self.started_at + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(DigestRunError, "already has an open"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="semantic_analysis",
                status="started",
                now=self.started_at + timedelta(seconds=3),
            )
        with self.assertRaisesRegex(DigestRunError, "open stages"):
            finish_run(
                self.kb,
                run_id=self.run_id,
                status="cancelled",
                error_code="USER_CANCELLED",
                now=self.started_at + timedelta(seconds=4),
            )

    def test_invalid_fields_and_terminal_mutation_fail_closed(self):
        self.start()
        with self.assertRaisesRegex(DigestRunError, "Unknown digest stage"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="free_form_secret",
                status="started",
            )
        with self.assertRaisesRegex(DigestRunError, "Unknown digest run metrics"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="capture",
                status="started",
                metrics={"secret_count": 1},
            )
        with self.assertRaisesRegex(DigestRunError, "require error_code"):
            finish_run(self.kb, run_id=self.run_id, status="failed")
        with self.assertRaisesRegex(DigestRunError, "require detail_code"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="capture",
                status="failed",
            )

        finish_run(
            self.kb,
            run_id=self.run_id,
            status="failed",
            error_code="SOURCE_CAPTURE_FAILED",
            now=self.started_at + timedelta(seconds=2),
        )
        with self.assertRaisesRegex(DigestRunError, "already terminal"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="capture",
                status="started",
            )

    def test_cli_returns_structured_errors_and_timeline(self):
        cli = ROOT / "bin/digest-run.py"
        started = subprocess.run(
            [
                sys.executable,
                str(cli),
                "start",
                "--kb",
                str(self.kb),
                "--source-type",
                "local_md",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, started.returncode, msg=started.stderr)
        payload = json.loads(started.stdout)
        self.assertRegex(payload["run_id"], r"^DG-")

        failed = subprocess.run(
            [
                sys.executable,
                str(cli),
                "stage",
                "--kb",
                str(self.kb),
                "--run-id",
                payload["run_id"],
                "--stage",
                "capture",
                "--status",
                "completed",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(2, failed.returncode)
        error = json.loads(failed.stdout)
        self.assertEqual("DIGEST_RUN_STAGE_NOT_STARTED", error["error"]["code"])
        self.assertEqual("", failed.stderr)

    def test_digest_transaction_records_preflight_stage_for_run_id(self):
        (self.kb / "raw_data").mkdir()
        (self.kb / "knowledge").mkdir()
        source_dir = Path(self.temporary.name) / "source"
        source_dir.mkdir()
        body = source_dir / "body.xml"
        comments = source_dir / "comments.json"
        manifest = source_dir / "source.json"
        body.write_text("<doc><p>content</p></doc>\n", encoding="utf-8")
        comments.write_text(
            '{"coverage":{"status":"complete"},"comments":[]}\n',
            encoding="utf-8",
        )
        manifest.write_text(
            json.dumps(
                {
                    "type": "feishu_doc",
                    "uid": "doc-test-timing",
                    "revision": "1",
                    "url": "https://example.test/docx/doc-test-timing",
                    "title": "timing test",
                    "comments_status": "complete",
                    "comment_count": 0,
                    "components": [
                        {
                            "name": "body",
                            "kind": "body",
                            "path": str(body),
                            "mode": "verbatim",
                        },
                        {
                            "name": "comments",
                            "kind": "comments",
                            "path": str(comments),
                            "mode": "canonical-json",
                            "json_pointer": "/comments",
                            "coverage": "complete",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.start()
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin/digest-txn.py"),
                "preflight",
                "--kb",
                str(self.kb),
                "--manifest",
                str(manifest),
                "--run-id",
                self.run_id,
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, msg=result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(self.run_id, payload["digest_run_id"])
        self.assertEqual("ok", payload["digest_run_logging"])
        shown = show_run(self.kb, run_id=self.run_id)
        preflight = shown["summary"]["stages"][0]
        self.assertEqual("preflight", preflight["stage"])
        self.assertEqual("completed", preflight["status"])


if __name__ == "__main__":
    unittest.main()
