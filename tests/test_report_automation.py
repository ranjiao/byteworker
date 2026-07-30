import json
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from report_automation import (  # noqa: E402
    ReportAutomationError,
    acquire_lease,
    complete_run,
    configure,
    record_decision,
    state_path,
    status,
)


class ReportAutomationTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        (kb / ".git" / "info" / "exclude").write_text(
            "# local excludes\n", encoding="utf-8"
        )
        return kb

    def test_missing_state_requests_onboarding_without_writing(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            payload = status(
                kb,
                now=datetime(2026, 7, 30, tzinfo=timezone.utc),
            )
            self.assertTrue(payload["needs_onboarding"])
            self.assertEqual("unasked", payload["decision"])
            self.assertFalse(state_path(kb).exists())
            self.assertIn(
                "/state/",
                (kb / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )

    def test_prompted_declined_or_deferred_decision_is_not_reprompted(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            for decision in ("prompted", "declined", "deferred"):
                with self.subTest(decision=decision):
                    payload = record_decision(kb, decision=decision)
                    self.assertEqual(decision, payload["decision"])
                    self.assertFalse(payload["needs_onboarding"])

    def test_configuration_requires_local_and_preserves_last_run(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(ReportAutomationError) as caught:
                configure(
                    kb,
                    harness="codex",
                    timezone_name="Asia/Shanghai",
                    environment="cloud",
                    daily_schedule="工作日 20:30",
                    weekly_schedule="周一 09:30",
                )
            self.assertEqual(
                "REPORT_AUTOMATION_CONFIG_INVALID",
                caught.exception.code,
            )

            configured = configure(
                kb,
                harness="codex",
                timezone_name="Asia/Shanghai",
                environment="local",
                daily_schedule="工作日 20:30",
                weekly_schedule="周一 09:30",
                daily_task_id="daily-1",
                weekly_task_id="weekly-1",
            )
            self.assertEqual("configured", configured["decision"])
            self.assertEqual("local", configured["environment"])
            self.assertEqual("daily-1", configured["daily"]["native_task_id"])
            self.assertFalse(configured["needs_onboarding"])

    def test_single_lease_blocks_overlap_and_expiry_allows_recovery(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
            lease = acquire_lease(
                kb,
                kind="daily",
                period="2026-07-30",
                owner="codex",
                lease_seconds=60,
                now=now,
            )
            with self.assertRaises(ReportAutomationError) as caught:
                acquire_lease(
                    kb,
                    kind="weekly",
                    period="2026-W31",
                    owner="claude",
                    lease_seconds=60,
                    now=now + timedelta(seconds=30),
                )
            self.assertEqual("REPORT_AUTOMATION_BUSY", caught.exception.code)

            replacement = acquire_lease(
                kb,
                kind="weekly",
                period="2026-W31",
                owner="claude",
                lease_seconds=60,
                now=now + timedelta(seconds=61),
            )
            self.assertNotEqual(lease["token"], replacement["token"])

    def test_lease_rejects_invalid_periods(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            for kind, period in (
                ("daily", "2026-02-30"),
                ("weekly", "2026-W99"),
                ("weekly", "2026-07-30"),
            ):
                with self.subTest(kind=kind, period=period):
                    with self.assertRaises(ReportAutomationError) as caught:
                        acquire_lease(
                            kb,
                            kind=kind,
                            period=period,
                            owner="codex",
                            lease_seconds=60,
                        )
                    self.assertEqual(
                        "REPORT_AUTOMATION_LEASE_INVALID",
                        caught.exception.code,
                    )

    def test_complete_records_success_or_failure_and_releases_lease(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
            lease = acquire_lease(
                kb,
                kind="daily",
                period="2026-07-30",
                owner="trae",
                lease_seconds=60,
                now=now,
            )
            run = complete_run(
                kb,
                token=lease["token"],
                run_status="success",
                report_path="reports/daily/2026-07-30.md",
                now=now + timedelta(seconds=10),
            )
            self.assertEqual("success", run["status"])
            payload = status(kb, now=now + timedelta(seconds=11))
            self.assertIsNone(payload["active_lease"])
            self.assertEqual(
                "reports/daily/2026-07-30.md",
                payload["daily"]["last_run"]["report_path"],
            )

            with self.assertRaises(ReportAutomationError) as caught:
                complete_run(
                    kb,
                    token=lease["token"],
                    run_status="failed",
                )
            self.assertEqual(
                "REPORT_AUTOMATION_LEASE_MISMATCH",
                caught.exception.code,
            )

    def test_complete_requires_a_real_result_shape(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            lease = acquire_lease(
                kb,
                kind="weekly",
                period="2026-W31",
                owner="claude",
                lease_seconds=60,
            )
            for run_status, report_path, error_code in (
                ("success", "", ""),
                ("success", "../weekly.md", ""),
                ("failed", "", ""),
            ):
                with self.subTest(run_status=run_status, report_path=report_path):
                    with self.assertRaises(ReportAutomationError) as caught:
                        complete_run(
                            kb,
                            token=lease["token"],
                            run_status=run_status,
                            report_path=report_path,
                            error_code=error_code,
                        )
                    self.assertEqual(
                        "REPORT_AUTOMATION_RUN_RESULT_INVALID",
                        caught.exception.code,
                    )
            failed = complete_run(
                kb,
                token=lease["token"],
                run_status="failed",
                error_code="SOURCE_AUTH_REQUIRED",
            )
            self.assertEqual("SOURCE_AUTH_REQUIRED", failed["error_code"])

    def test_direct_cli_and_machine_facade_return_structured_json(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            direct = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "report-automation.py"),
                    "status",
                    "--kb",
                    str(kb),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, direct.returncode, direct.stderr)
            self.assertTrue(json.loads(direct.stdout)["needs_onboarding"])

            facade = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "byteworker-cli.py"),
                    "report-automation",
                    "status",
                    "--kb",
                    str(kb),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, facade.returncode, facade.stderr)
            payload = json.loads(facade.stdout)
            self.assertEqual("success", payload["status"])
            self.assertTrue(payload["data"]["needs_onboarding"])


if __name__ == "__main__":
    unittest.main()
