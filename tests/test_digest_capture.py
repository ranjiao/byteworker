import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "lib"))

from digest_capture import DigestCaptureError, execute_capture_plan  # noqa: E402


class Result:
    def __init__(self, returncode=0):
        self.returncode = returncode
        self.stderr = b""


class DigestCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.skill = self.root / "skill"
        (self.skill / "bin").mkdir(parents=True)
        (self.skill / "bin" / "byteworker").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.skill / "bin" / "pull_doc_comments.py").write_text("", encoding="utf-8")
        self.request = self.root / "capture-plan.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_request(self, jobs, max_workers=3):
        self.request.write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-digest-capture-plan/v1",
                    "max_workers": max_workers,
                    "jobs": jobs,
                }
            ),
            encoding="utf-8",
        )

    def test_executes_jobs_concurrently_and_preserves_private_outputs(self):
        active = 0
        peak = 0
        lock = threading.Lock()

        def runner(command, *, stdout, **kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.04)
            stdout.write(command[-1].encode("utf-8"))
            with lock:
                active -= 1
            return Result()

        jobs = [
            {
                "id": f"job-{index}",
                "runner": "lark",
                "args": ["docs", "+fetch", "--doc", f"value-{index}"],
                "output": str(self.root / f"output-{index}.json"),
                "max_attempts": 1,
            }
            for index in range(3)
        ]
        self.write_request(jobs)

        receipt = execute_capture_plan(
            self.request,
            skill_root=self.skill,
            runner=runner,
            sleeper=lambda _: None,
        )

        self.assertEqual("completed", receipt["status"])
        self.assertEqual(3, receipt["job_count"])
        self.assertGreaterEqual(peak, 2)
        self.assertEqual(
            ["job-0", "job-1", "job-2"],
            [item["id"] for item in receipt["jobs"]],
        )
        for index in range(3):
            output = self.root / f"output-{index}.json"
            self.assertEqual(f"value-{index}", output.read_text(encoding="utf-8"))
            self.assertEqual(0o600, os.stat(output).st_mode & 0o777)

    def test_retries_failed_read_without_exposing_stderr(self):
        attempts = 0

        def runner(command, *, stdout, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return Result(returncode=9)
            stdout.write(b"complete")
            return Result()

        self.write_request(
            [
                {
                    "id": "body",
                    "runner": "lark",
                    "args": ["docs", "+fetch"],
                    "output": str(self.root / "body.json"),
                    "max_attempts": 2,
                }
            ]
        )
        receipt = execute_capture_plan(
            self.request,
            skill_root=self.skill,
            runner=runner,
            sleeper=lambda _: None,
        )
        self.assertEqual(2, receipt["jobs"][0]["attempts"])
        self.assertEqual("complete", (self.root / "body.json").read_text())

    def test_rejects_outputs_inside_skill_repo(self):
        self.write_request(
            [
                {
                    "id": "body",
                    "runner": "lark",
                    "args": ["docs", "+fetch"],
                    "output": str(self.skill / "private.json"),
                }
            ]
        )
        with self.assertRaises(DigestCaptureError) as caught:
            execute_capture_plan(self.request, skill_root=self.skill)
        self.assertEqual("DIGEST_CAPTURE_PATH_IN_SKILL_REPO", caught.exception.code)

    def test_rejects_unbounded_worker_count(self):
        self.write_request(
            [
                {
                    "id": "body",
                    "runner": "lark",
                    "args": ["docs", "+fetch"],
                    "output": str(self.root / "body.json"),
                }
            ],
            max_workers=5,
        )
        with self.assertRaises(DigestCaptureError):
            execute_capture_plan(self.request, skill_root=self.skill)

    def test_rejects_lark_write_operations(self):
        self.write_request(
            [
                {
                    "id": "write",
                    "runner": "lark",
                    "args": ["minutes", "+update", "--yes"],
                    "output": str(self.root / "unsafe.json"),
                }
            ]
        )
        with self.assertRaises(DigestCaptureError) as caught:
            execute_capture_plan(self.request, skill_root=self.skill)
        self.assertEqual("DIGEST_CAPTURE_PLAN_INVALID", caught.exception.code)

    def test_failed_job_removes_stale_output_and_fails_closed(self):
        output = self.root / "body.json"
        output.write_text("stale", encoding="utf-8")
        self.write_request(
            [
                {
                    "id": "body",
                    "runner": "lark",
                    "args": ["docs", "+fetch"],
                    "output": str(output),
                    "max_attempts": 1,
                }
            ]
        )

        with self.assertRaises(DigestCaptureError) as caught:
            execute_capture_plan(
                self.request,
                skill_root=self.skill,
                runner=lambda *args, **kwargs: Result(returncode=7),
                sleeper=lambda _: None,
            )
        self.assertEqual("DIGEST_CAPTURE_JOB_FAILED", caught.exception.code)
        self.assertFalse(output.exists())

    def test_comments_runner_uses_allowlisted_helper(self):
        commands = []

        def runner(command, *, stdout, **kwargs):
            commands.append(command)
            stdout.write(b"{}")
            return Result()

        self.write_request(
            [
                {
                    "id": "comments",
                    "runner": "comments",
                    "args": ["--url", "https://example.test/docx/a"],
                    "output": str(self.root / "comments.json"),
                }
            ]
        )
        execute_capture_plan(
            self.request,
            skill_root=self.skill,
            runner=runner,
            sleeper=lambda _: None,
        )
        self.assertTrue(commands[0][1].endswith("pull_doc_comments.py"))


if __name__ == "__main__":
    unittest.main()
