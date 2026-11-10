import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from digest_run_log import finish_run, record_stage, start_run, wait_for_user  # noqa: E402


class DigestFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.kb = Path(temporary.name) / "private-kb"
        self.kb.mkdir()
        subprocess.run(["git", "init", "-q", self.kb], check=True)

    def invoke(self, operation, run_id=None, *, recipient=None):
        env = dict(os.environ, BYTEWORKER_PYTHON_BIN=sys.executable)
        env.pop("BYTEWORKER_FEEDBACK_EMAIL", None)
        if recipient is not None:
            env["BYTEWORKER_FEEDBACK_EMAIL"] = recipient
        args = ["doctor", operation, "--kb", str(self.kb)]
        if run_id is not None:
            args += ["--run-id", run_id]
        result = subprocess.run(
            [ROOT / "bin/byteworker", *args], env=env, capture_output=True, text=True,
            check=False,
        )
        self.assertTrue(result.stdout, result.stderr)
        return result.returncode, json.loads(result.stdout)

    def bundle(self, run_id):
        exit_code, response = self.invoke("bundle", run_id)
        self.assertEqual(0, exit_code, response)
        data = response["data"]
        path = Path(data["path"])
        self.addCleanup(path.unlink, missing_ok=True)
        self.assertFalse(path.resolve().is_relative_to(self.kb))
        self.assertFalse(path.resolve().is_relative_to(ROOT))
        self.assertEqual(0o600, path.stat().st_mode & 0o777)
        with ZipFile(path) as archive:
            self.assertEqual(data["contents"], archive.namelist())
            self.assertEqual(["summary.json", "timeline.jsonl"], archive.namelist())
            self.assertEqual(data["summary"], json.loads(archive.read("summary.json")))
            contents = "\n".join(archive.read(name).decode() for name in archive.namelist())
        return data, contents

    def test_exports_only_allowlisted_technical_metadata_for_one_run(self):
        secret = "https://internal.invalid/knowledge?token=secret"
        run = start_run(self.kb, source_type="feishu_doc", source_ref=secret)
        other = start_run(self.kb, source_type="feishu_doc")
        run_id = run["run_id"]
        record_stage(
            self.kb, run_id=run_id, stage="capture", status="started",
            metrics={"input_bytes": 98654321},
        )
        record_stage(
            self.kb, run_id=run_id, stage="capture", status="failed",
            detail_code="PRIVATE_PROJECT_SECRET",
        )
        finish_run(self.kb, run_id=run_id, status="failed", error_code="SOURCE_CAPTURE_FAILED")
        (self.kb / "INDEX.md").write_text("CONFIDENTIAL_INDEX")
        log_path = next((self.kb / "state/digest/run-logs").glob("*.jsonl"))
        rows = [json.loads(line) for line in log_path.read_text().splitlines()]
        rows[0].update(
            source_title="CONFIDENTIAL_TITLE", url=secret,
            stdout="SECRET_STDOUT", artifact_path=str(self.kb / "secret.md"),
        )
        rows.append(dict(run, event="usage_recorded", usage={"input_tokens": 98654321}))
        log_path.write_text("".join(json.dumps(row) + "\n" for row in rows))

        exit_code, response = self.invoke("feedback", run_id)
        self.assertEqual(0, exit_code, response)
        draft = response["data"]
        self.assertEqual("feedback", response["context"]["operation"])
        self.assertEqual("ranjiao@gmail.com", draft["to"])
        self.assertIn("capture", draft["body"])
        self.assertIn("unknown", draft["body"])
        self.assertIn("预期：\n实际：", draft["body"])
        link = urlsplit(draft["mailto"])
        self.assertEqual("mailto", link.scheme)
        self.assertEqual(draft["to"], link.path)
        self.assertEqual(
            {"subject": [draft["subject"]], "body": [draft["body"].replace("\n", "\r\n")]},
            parse_qs(link.query),
        )
        self.assertEqual({"schema_version", "run_id", "to", "subject", "body", "mailto"}, set(draft))
        data, contents = self.bundle(run_id)
        self.assertEqual(4, data["timeline_event_count"])
        self.assertEqual("capture", data["summary"]["problem_stage"])
        self.assertEqual("SOURCE_CAPTURE_FAILED", data["summary"]["reason_code"])
        combined = contents + json.dumps(draft)
        for forbidden in (
            secret, hashlib.sha256(secret.encode()).hexdigest(), str(self.kb), other["run_id"],
            "feishu_doc", "98654321", "source_type", "usage", "CONFIDENTIAL_INDEX",
            "CONFIDENTIAL_TITLE", "PRIVATE_PROJECT_SECRET", "SECRET_STDOUT", "secret.md",
        ):
            self.assertNotIn(forbidden, combined)

    def test_recipient_override_and_invalid_address(self):
        run_id = start_run(self.kb, source_type="unknown")["run_id"]
        code, response = self.invoke("feedback", run_id, recipient="support+digest@example.com")
        self.assertEqual(0, code, response)
        self.assertEqual("support+digest@example.com", response["data"]["to"])
        self.assertIn("support%2Bdigest@example.com", response["data"]["mailto"])
        code, response = self.invoke("feedback", run_id, recipient="one@example.com\nbcc:secret")
        self.assertEqual(1, code, response)
        self.assertEqual("error", response["status"])
        self.assertNotIn("bcc:secret", json.dumps(response))
        code, response = self.invoke("feedback")
        self.assertEqual(1, code, response)
        self.assertEqual("DIGEST_FEEDBACK_RUN_REQUIRED", response["error"]["code"])
        code, response = self.invoke("bundle", "../../foo")
        self.assertEqual(1, code, response)
        self.assertEqual("DIGEST_RUN_INVALID_ID", response["error"]["code"])

    def test_successful_and_noop_runs_can_be_reported(self):
        for status in ("committed", "noop"):
            with self.subTest(status=status):
                run_id = start_run(self.kb, source_type="local_md")["run_id"]
                finish_run(self.kb, run_id=run_id, status=status)
                data, _ = self.bundle(run_id)
                self.assertEqual(status, data["summary"]["transaction_outcome"])
                self.assertEqual(status, data["summary"]["status"])
                self.assertIsNone(data["summary"]["reason_code"])

    def test_run_version_ignores_knowledge_base_git_override_and_supports_old_logs(self):
        subprocess.run(
            ["git", "-C", self.kb, "-c", "user.name=Tester", "-c", "user.email=test@example.com",
             "-c", "commit.gpgsign=false", "commit", "-qm", "Fixture", "--allow-empty"],
            check=True,
        )
        kb_head = subprocess.check_output(["git", "-C", self.kb, "rev-parse", "HEAD"], text=True).strip()
        with patch.dict(os.environ, {"GIT_DIR": str(self.kb / ".git")}):
            run = start_run(self.kb, source_type="unknown")
            data, contents = self.bundle(run["run_id"])
        self.assertNotIn(kb_head, contents)
        self.assertEqual(run.get("skill_revision"), data["summary"]["export_revision"])
        log_path = next((self.kb / "state/digest/run-logs").glob("*.jsonl"))
        row = json.loads(log_path.read_text())
        row.pop("skill_revision", None)
        log_path.write_text(json.dumps(row) + "\n")
        data, _ = self.bundle(run["run_id"])
        self.assertIsNone(data["summary"]["run_revision"])
        self.assertIn("RUN_VERSION_NOT_RECORDED", data["summary"]["diagnostic_gaps"])

    def test_partial_interrupted_missing_and_waiting_logs(self):
        now = datetime.now(timezone.utc) - timedelta(hours=7)
        interrupted = start_run(self.kb, source_type="local_md", now=now)["run_id"]
        record_stage(
            self.kb, run_id=interrupted, stage="transaction", status="started",
            now=now + timedelta(seconds=1),
        )
        waiting = start_run(self.kb, source_type="unknown")["run_id"]
        wait_for_user(self.kb, run_id=waiting, reason_code="SOURCE_AUTH_REQUIRED")
        log_path = next((self.kb / "state/digest/run-logs").glob("*.jsonl"))
        with log_path.open("a") as handle:
            handle.write("{PARTIAL_PRIVATE_ROW\n")
        data, contents = self.bundle(interrupted)
        self.assertEqual("stale", data["summary"]["status"])
        self.assertEqual("unknown", data["summary"]["transaction_outcome"])
        self.assertIn("LOG_RECORD_INVALID", data["summary"]["diagnostic_gaps"])
        self.assertIn("RUN_TERMINAL_NOT_RECORDED", data["summary"]["diagnostic_gaps"])
        self.assertNotIn("PARTIAL_PRIVATE_ROW", contents)
        data, _ = self.bundle(waiting)
        self.assertEqual("waiting_user", data["summary"]["status"])
        self.assertEqual("SOURCE_AUTH_REQUIRED", data["summary"]["reason_code"])
        data, _ = self.bundle("DG-20260101T000000Z-00000000")
        self.assertIn("RUN_LOG_NOT_FOUND", data["summary"]["diagnostic_gaps"])
        self.assertEqual("unknown", data["summary"]["transaction_outcome"])
        self.assertEqual(0, data["timeline_event_count"])

    def test_transaction_failure_preserves_uncertainty_about_writes(self):
        run_id = start_run(self.kb, source_type="local_md")["run_id"]
        record_stage(self.kb, run_id=run_id, stage="transaction", status="started")
        record_stage(
            self.kb, run_id=run_id, stage="transaction", status="failed",
            detail_code="DIGEST_FLOW_TRANSACTION_FAILED",
        )
        data, _ = self.bundle(run_id)
        self.assertEqual("failed", data["summary"]["transaction_outcome"])
        self.assertIn("RUN_TERMINAL_NOT_RECORDED", data["summary"]["diagnostic_gaps"])
