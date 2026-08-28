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
    record_heartbeat,
    record_stage,
    record_usage,
    resume_run,
    show_run,
    start_run,
    wait_for_user,
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
            metrics={
                "component_count": 3,
                "input_bytes": 4096,
                "worker_count": 3,
                "shard_count": 3,
            },
            now=self.started_at + timedelta(seconds=7, milliseconds=250),
        )
        self.assertEqual(6250, completed["duration_ms"])
        self.assertEqual(3, completed["metrics"]["worker_count"])

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

    def test_stale_run_stops_active_duration_at_last_lifecycle_event(self):
        self.start()
        record_stage(
            self.kb,
            run_id=self.run_id,
            stage="capture",
            status="started",
            now=self.started_at + timedelta(minutes=1),
        )
        heartbeat = record_heartbeat(
            self.kb,
            run_id=self.run_id,
            stage="capture",
            now=self.started_at + timedelta(hours=5),
        )
        self.assertEqual("heartbeat", heartbeat["event"])
        active = show_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(hours=10),
        )["summary"]
        self.assertEqual("running", active["status"])
        self.assertEqual(10 * 60 * 60 * 1000, active["duration_ms"])

        stale = show_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(hours=12),
        )["summary"]
        self.assertEqual("stale", stale["status"])
        self.assertEqual(5 * 60 * 60 * 1000, stale["duration_ms"])
        self.assertEqual(6 * 60 * 60, stale["stale_after_seconds"])
        self.assertTrue(stale["stale_since"])
        with self.assertRaisesRegex(DigestRunError, "stale"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="capture",
                status="completed",
                now=self.started_at + timedelta(hours=12),
            )
        resume_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(hours=12),
        )
        completed = record_stage(
            self.kb,
            run_id=self.run_id,
            stage="capture",
            status="completed",
            now=self.started_at + timedelta(hours=12, seconds=1),
        )
        self.assertEqual((4 * 60 * 60 + 59 * 60 + 1) * 1000, completed["duration_ms"])

    def test_waiting_user_and_resume_exclude_wait_time_from_active_duration(self):
        self.start()
        waiting = wait_for_user(
            self.kb,
            run_id=self.run_id,
            reason_code="DEPENDENCY_APPROVAL_REQUIRED",
            now=self.started_at + timedelta(seconds=10),
        )
        self.assertEqual("waiting_user", waiting["status"])
        paused = show_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(days=2),
        )["summary"]
        self.assertEqual("waiting_user", paused["status"])
        self.assertEqual(10_000, paused["duration_ms"])
        self.assertEqual(
            "DEPENDENCY_APPROVAL_REQUIRED", paused["waiting_reason_code"]
        )
        with self.assertRaisesRegex(DigestRunError, "waiting for user"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="semantic_analysis",
                status="started",
                now=self.started_at + timedelta(days=2),
            )

        resumed_at = self.started_at + timedelta(days=2)
        resumed = resume_run(self.kb, run_id=self.run_id, now=resumed_at)
        self.assertEqual("resumed", resumed["event"])
        finish_run(
            self.kb,
            run_id=self.run_id,
            status="noop",
            now=resumed_at + timedelta(seconds=5),
        )
        final = show_run(self.kb, run_id=self.run_id)["summary"]
        self.assertEqual("noop", final["status"])
        self.assertEqual(15_000, final["duration_ms"])

    def test_resume_reactivates_stale_run(self):
        self.start()
        resumed = resume_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(hours=7),
        )
        self.assertEqual("resumed", resumed["event"])
        summary = show_run(
            self.kb,
            run_id=self.run_id,
            now=self.started_at + timedelta(hours=7, seconds=2),
        )["summary"]
        self.assertEqual("running", summary["status"])
        self.assertEqual(2_000, summary["duration_ms"])

    def test_usage_receipts_aggregate_and_deduplicate_after_terminal(self):
        self.start()
        first = record_usage(
            self.kb,
            run_id=self.run_id,
            stage="semantic_analysis",
            worker_role="semantic_worker",
            usage_source="measured",
            call_id="call-semantic-001",
            usage={
                "input_tokens": 1200,
                "cached_input_tokens": 900,
                "output_tokens": 150,
                "reasoning_tokens": 40,
            },
            now=self.started_at + timedelta(seconds=1),
        )
        duplicate = record_usage(
            self.kb,
            run_id=self.run_id,
            stage="semantic_analysis",
            worker_role="semantic_worker",
            usage_source="measured",
            call_id="call-semantic-001",
            usage={
                "input_tokens": 1200,
                "cached_input_tokens": 900,
                "output_tokens": 150,
                "reasoning_tokens": 40,
            },
            now=self.started_at + timedelta(seconds=2),
        )
        self.assertFalse(first["deduplicated"])
        self.assertTrue(duplicate["deduplicated"])

        finish_run(
            self.kb,
            run_id=self.run_id,
            status="committed",
            now=self.started_at + timedelta(seconds=3),
        )
        record_usage(
            self.kb,
            run_id=self.run_id,
            stage="finalize",
            worker_role="coordinator",
            usage_source="estimated",
            call_id="call-finalize-001",
            usage={
                "input_tokens": 500,
                "cached_input_tokens": 0,
                "output_tokens": 80,
                "reasoning_tokens": 10,
            },
            now=self.started_at + timedelta(seconds=4),
        )
        summary = show_run(self.kb, run_id=self.run_id)["summary"]
        self.assertEqual("committed", summary["status"])
        self.assertEqual(3000, summary["duration_ms"])
        self.assertEqual(2, summary["usage"]["model_calls"])
        self.assertEqual(1, summary["usage"]["measured_calls"])
        self.assertEqual(1, summary["usage"]["estimated_calls"])
        self.assertEqual(1700, summary["usage"]["input_tokens"])
        self.assertEqual(230, summary["usage"]["output_tokens"])
        self.assertEqual(1930, summary["usage"]["total_tokens"])
        self.assertEqual(
            1,
            summary["usage"]["by_stage"]["semantic_analysis"]["measured_calls"],
        )

        log_text = next((self.kb / "state/digest/run-logs").glob("*.jsonl")).read_text()
        self.assertNotIn("call-semantic-001", log_text)
        self.assertIn("call_id_hash", log_text)
        with self.assertRaisesRegex(DigestRunError, "already terminal"):
            record_stage(
                self.kb,
                run_id=self.run_id,
                stage="finalize",
                status="started",
            )

    def test_usage_receipts_reject_invalid_values(self):
        self.start()
        with self.assertRaisesRegex(DigestRunError, "cannot exceed"):
            record_usage(
                self.kb,
                run_id=self.run_id,
                stage="semantic_analysis",
                worker_role="coordinator",
                usage_source="measured",
                call_id="call-1",
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 11,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                },
            )
        with self.assertRaisesRegex(DigestRunError, "call_id"):
            record_usage(
                self.kb,
                run_id=self.run_id,
                stage="semantic_analysis",
                worker_role="coordinator",
                usage_source="estimated",
                call_id="secret/value",
                usage={
                    "input_tokens": 10,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                },
            )

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

        usage = subprocess.run(
            [
                sys.executable,
                str(cli),
                "usage",
                "--kb",
                str(self.kb),
                "--run-id",
                payload["run_id"],
                "--stage",
                "semantic_analysis",
                "--worker-role",
                "coordinator",
                "--usage-source",
                "measured",
                "--call-id",
                "cli-call-1",
                "--input-tokens",
                "100",
                "--cached-input-tokens",
                "80",
                "--output-tokens",
                "20",
                "--reasoning-tokens",
                "5",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, usage.returncode, msg=usage.stderr)
        usage_payload = json.loads(usage.stdout)
        self.assertEqual("usage_recorded", usage_payload["event"])
        self.assertNotIn("cli-call-1", usage.stdout)

        waiting = subprocess.run(
            [
                sys.executable,
                str(cli),
                "wait",
                "--kb",
                str(self.kb),
                "--run-id",
                payload["run_id"],
                "--reason-code",
                "USER_INPUT_REQUIRED",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, waiting.returncode, waiting.stdout)
        self.assertEqual("waiting_user", json.loads(waiting.stdout)["status"])
        resumed = subprocess.run(
            [
                sys.executable,
                str(cli),
                "resume",
                "--kb",
                str(self.kb),
                "--run-id",
                payload["run_id"],
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, resumed.returncode, resumed.stdout)
        self.assertEqual("resumed", json.loads(resumed.stdout)["event"])

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
        resume_run(self.kb, run_id=self.run_id, now=datetime.now(timezone.utc))
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
