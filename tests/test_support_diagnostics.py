import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ROOT / "lib"))

import operation_trace as trace  # noqa: E402
from dreaming_delivery_lark import deliver_lark_bot_summary  # noqa: E402
from dreaming_diagnostics import diagnose, observed_run_due  # noqa: E402
from dreaming_reports import enqueue_delivery  # noqa: E402
from dreaming_run_log import append_run_event  # noqa: E402
from dreaming_scheduler import complete_run, enable, heartbeat_run  # noqa: E402
from dreaming_state import DreamingError, atomic_write_json, state_path  # noqa: E402
from support_feedback import generate  # noqa: E402


class SupportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.kb = Path(temporary.name) / "private-kb"
        (self.kb / ".git/info").mkdir(parents=True)
        self.env = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(Path(temporary.name) / "state"),
            "BYTEWORKER_PYTHON_BIN": sys.executable,
            "BYTEWORKER_NO_AUTO_UPDATE": "1",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def cli(self, *args, trace_id=""):
        completed = subprocess.run(
            [ROOT / "bin/byteworker", *args],
            env=dict(os.environ, BYTEWORKER_TRACE_ID=trace_id),
            capture_output=True, text=True, check=False,
        )
        self.assertTrue(completed.stdout, completed.stderr)
        return completed.returncode, json.loads(completed.stdout)

    def activate(self, *, minutes_ago):
        now = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
        result = enable(
            self.kb, harness="trae", timezone_name="Asia/Shanghai",
            acknowledge_capability_tour=True, acknowledge_machine_runtime=True,
            acknowledge_schedule=True, now=now,
        )
        for job in result["jobs"].values():
            job["enabled"] = False
        result["jobs"]["process"]["enabled"] = True
        atomic_write_json(state_path(self.kb), result)

    def export(self, run_id):
        bundle = generate(self.kb, run_id=run_id, root=ROOT, bundle=True)
        path = Path(bundle["path"])
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        with ZipFile(io.BytesIO(path.read_bytes())) as archive:
            self.assertEqual(["summary.json", "timeline.jsonl"], archive.namelist())
            return bundle["summary"], "\n".join(archive.read(name).decode() for name in archive.namelist())

    def test_operation_trace_uses_actual_facade_and_excludes_query_and_payload(self):
        _, started = self.cli("doctor", "trace-start", "--kind", "search", "--kb", str(self.kb))
        trace_id = started["data"]["run_id"]
        (self.kb / "knowledge").mkdir()
        code, found = self.cli("kb-query", "search", "--kb", str(self.kb), "--query", "PRIVATE_KEYWORD",
                               trace_id=trace_id)
        self.assertEqual(0, code, found)
        self.assertEqual(trace_id, found["context"]["trace_id"])
        _, receipt = self.cli("doctor", "trace-finish", "--run-id", trace_id, "--result", "insufficient_evidence")
        self.assertEqual("success", receipt["status"])
        _, draft = self.cli("doctor", "feedback", "--run-id", trace_id)
        self.assertIn("insufficient_evidence", draft["data"]["body"])
        summary, exported = self.export(trace_id)
        self.assertEqual("insufficient_evidence", summary["status"])
        self.assertIn("no_match", exported)
        for private in ("PRIVATE_KEYWORD", str(self.kb), trace.scope(self.kb), "candidates", "coverage"):
            self.assertNotIn(private, exported + draft["data"]["body"])

    def test_no_wake_and_real_preflight_failure(self):
        payload = diagnose(self.kb)
        self.assertFalse(state_path(self.kb).exists())
        self.assertIn("WAKE_NOT_OBSERVED", payload["diagnostic_gaps"])
        code, response = self.cli("dreaming", "tick", "--kb", str(self.kb), "--owner", "PRIVATE_HOST")
        self.assertEqual(0, code, response)
        tick = response["data"]
        self.assertEqual("preflight_failed", tick["status"])
        report = diagnose(self.kb, tick_id=tick["tick_id"])
        self.assertIsNone(report["run"])
        self.assertIn("CONTEXT_MISSING", {event.get("code") for event in report["tick"]["events"]})
        self.assertNotIn("PRIVATE_HOST", json.dumps(report))

    def test_real_tick_records_successful_preflight_and_scheduler_decision(self):
        self.activate(minutes_ago=0)
        (self.kb / "context.md").write_text("# Context\n")
        code, response = self.cli("dreaming", "tick", "--kb", str(self.kb), "--owner", "task")
        self.assertEqual(0, code, response)
        tick = response["data"]
        self.assertEqual("idle", tick["status"])
        report = diagnose(self.kb, tick_id=tick["tick_id"])
        self.assertIn(
            ("preflight", "success"),
            {(event["stage"], event["status"]) for event in report["tick"]["events"]},
        )
        self.assertIn("idle", {event["status"] for event in report["tick"]["events"]})

    def test_scheduler_reasons_and_explicit_digest_link(self):
        self.activate(minutes_ago=0)
        tick_id = trace.start("dreaming", self.kb)
        result = observed_run_due(self.kb, owner="private-task", lease_seconds=100,
                                  followup="", tick_id=tick_id)
        self.assertEqual("idle", result["status"])
        events = diagnose(self.kb, tick_id=tick_id)["tick"]["events"]
        self.assertTrue(any(event.get("job") == "process" and event["status"] == "not_due" for event in events))
        self.activate(minutes_ago=240)
        tick_id = trace.start("dreaming", self.kb)
        result = observed_run_due(self.kb, owner="private-task", lease_seconds=100,
                                  followup="", tick_id=tick_id)
        lease = result["lease"]
        digest_run_id = "DG-20261109T010101Z-abcdef12"
        heartbeat_run(self.kb, token=lease["token"], stage="collection", digest_run_id=digest_run_id)
        failure = "DIGEST_FLOW_COMMIT_FAILED"
        complete_run(self.kb, token=lease["token"], run_status="failed", error_code=failure)
        report = diagnose(self.kb)
        self.assertEqual(tick_id, report["tick"]["run_id"])
        self.assertEqual(lease["run_id"], report["run"]["run_id"])
        self.assertEqual("failed", report["run"]["status"])
        self.assertEqual(digest_run_id, report["run"]["events"][-2]["digest_run_id"])
        self.assertEqual(failure, report["run"]["events"][-1]["code"])
        summary, contents = self.export(tick_id)
        self.assertEqual(failure, summary["reason_code"])
        self.assertNotIn("private-task", contents)
        self.assertNotIn(lease["token"], contents)

    def test_lease_state_survives_logging_failure_and_expiry_is_read_only(self):
        self.activate(minutes_ago=240)
        tick_id = trace.start("dreaming", self.kb)
        with patch("dreaming_scheduler.append_run_event", side_effect=OSError("private log error")):
            with self.assertRaises(OSError):
                observed_run_due(self.kb, owner="task", lease_seconds=100, followup="", tick_id=tick_id)
        state = json.loads(state_path(self.kb).read_text())
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        state["active_lease"]["expires_at"] = expired
        state["jobs"]["process"]["last_attempt"]["lease_expires_at"] = expired
        atomic_write_json(state_path(self.kb), state)
        before = state_path(self.kb).read_bytes()
        report = diagnose(self.kb, tick_id=tick_id)
        self.assertEqual("interrupted", report["run"]["status"])
        self.assertIn("RUN_STATE_WITHOUT_MATCHING_LOG", report["diagnostic_gaps"])
        self.assertEqual(before, state_path(self.kb).read_bytes())

    def test_morning_wake_records_actual_dependency_block(self):
        self.activate(minutes_ago=24 * 60)
        state = json.loads(state_path(self.kb).read_text())
        state["jobs"]["process"]["enabled"] = False
        state["jobs"]["morning"]["enabled"] = True
        atomic_write_json(state_path(self.kb), state)
        tick_id = trace.start("dreaming", self.kb)
        dependency = {"status": "blocked", "blockers": [{"key": "PRIVATE_SOURCE_HASH"}]}
        with patch("dreaming_reports.report_dependency_from_state", return_value=dependency):
            result = observed_run_due(self.kb, owner="task", lease_seconds=100, followup="", tick_id=tick_id)
        self.assertEqual("idle", result["status"])
        report = diagnose(self.kb, tick_id=tick_id)
        events = report["tick"]["events"]
        self.assertTrue(any(
            event.get("job") == "morning" and event["status"] == "blocked_by_dependency"
            for event in events
        ))
        _, contents = self.export(tick_id)
        self.assertNotIn("PRIVATE_SOURCE_HASH", contents)

    def test_generated_report_and_unsent_delivery_are_separate(self):
        self.activate(minutes_ago=0)
        run_id = "DR-" + "9" * 32
        append_run_event(
            self.kb, run_id=run_id, event="completed", job="morning", period="2026-11-10",
            owner="PRIVATE_OWNER", epoch=1, stage="complete", status="success",
            artifact_path="reports/morning/2026-11-10.md", batch_id="EB-" + "2" * 32,
            metrics={"item_count": 98765432},
        )
        queued = enqueue_delivery(
            self.kb, kind="morning", period="2026-11-10", report_path="reports/morning/2026-11-10.md",
            commit=run_id, channel="lark_bot", artifact="summary", recipient_id="ou_PRIVATE_USER",
        )
        with self.assertRaises(DreamingError):
            deliver_lark_bot_summary(self.kb, outbox_id=queued["outbox_id"])
        report = diagnose(self.kb, run_id=run_id)
        self.assertEqual("success", report["run"]["status"])
        self.assertEqual("pending", report["run"]["delivery"])
        self.assertEqual("DREAMING_DELIVERY_ARTIFACT_MISSING", report["run"]["delivery_code"])
        summary, contents = self.export(run_id)
        self.assertEqual("pending", summary["delivery"])
        self.assertEqual("success", summary["status"])
        for private in ("PRIVATE_OWNER", "ou_PRIVATE_USER", "reports/", "EB-" + "2" * 32, "98765432", queued["outbox_id"]):
            self.assertNotIn(private, contents)

    def test_dreaming_tick_scope_is_not_inferred_from_another_knowledge_base(self):
        tick_id = trace.start("dreaming", self.kb)
        other = self.kb.parent / "other"
        other.mkdir()
        report = diagnose(other, tick_id=tick_id)
        self.assertIsNone(report["tick"])
        self.assertIn("WAKE_LINK_NOT_RECORDED", report["diagnostic_gaps"])
