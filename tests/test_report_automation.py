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
    check_period,
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
                recovery_schedule="每天 08:30/12:30/18:30/22:30",
                recovery_task_id="recovery-1",
            )
            self.assertEqual("configured", configured["decision"])
            self.assertEqual("local", configured["environment"])
            self.assertEqual("daily-1", configured["daily"]["native_task_id"])
            self.assertTrue(configured["recovery"]["enabled"])
            self.assertEqual(
                "recovery-1",
                configured["recovery"]["native_task_id"],
            )
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
            running = status(kb, now=now)
            self.assertEqual(
                "running",
                running["daily"]["last_attempt"]["status"],
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
            self.assertEqual(
                payload["daily"]["last_run"],
                payload["daily"]["last_success"],
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

    def test_failure_does_not_overwrite_last_success(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
            first = acquire_lease(
                kb,
                kind="daily",
                period="2026-07-29",
                owner="codex",
                lease_seconds=60,
                now=now,
            )
            successful = complete_run(
                kb,
                token=first["token"],
                run_status="success",
                report_path="reports/daily/2026-07-29.md",
                now=now + timedelta(seconds=10),
            )
            second = acquire_lease(
                kb,
                kind="daily",
                period="2026-07-30",
                owner="codex",
                lease_seconds=60,
                now=now + timedelta(seconds=20),
            )
            complete_run(
                kb,
                token=second["token"],
                run_status="failed",
                error_code="SOURCE_NETWORK_ERROR",
                now=now + timedelta(seconds=30),
            )
            payload = status(kb, now=now + timedelta(seconds=31))
            self.assertEqual("failed", payload["daily"]["last_run"]["status"])
            self.assertEqual(successful, payload["daily"]["last_success"])

    def test_check_period_reports_disabled_busy_complete_and_due(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

            disabled = check_period(
                kb,
                kind="daily",
                period="2026-07-30",
                now=now,
            )
            self.assertEqual("disabled", disabled["status"])
            self.assertFalse(disabled["should_run"])

            configure(
                kb,
                harness="codex",
                timezone_name="Asia/Shanghai",
                environment="local",
                daily_schedule="工作日 20:30",
                weekly_schedule="周一 09:30",
                now=now,
            )
            due = check_period(
                kb,
                kind="daily",
                period="2026-07-30",
                now=now,
            )
            self.assertEqual("due", due["status"])
            self.assertEqual("period_not_succeeded", due["reason"])

            lease = acquire_lease(
                kb,
                kind="daily",
                period="2026-07-30",
                owner="codex",
                lease_seconds=60,
                now=now,
            )
            busy = check_period(
                kb,
                kind="daily",
                period="2026-07-30",
                now=now + timedelta(seconds=10),
            )
            self.assertEqual("busy", busy["status"])
            self.assertFalse(busy["should_run"])
            self.assertNotIn("token", busy["active_lease"])

            complete_run(
                kb,
                token=lease["token"],
                run_status="success",
                report_path="reports/daily/2026-07-30.md",
                now=now + timedelta(seconds=20),
            )
            complete = check_period(
                kb,
                kind="daily",
                period="2026-07-30",
                now=now + timedelta(seconds=21),
            )
            self.assertEqual("complete", complete["status"])
            self.assertFalse(complete["should_run"])

            next_period = check_period(
                kb,
                kind="daily",
                period="2026-07-31",
                now=now + timedelta(seconds=22),
            )
            self.assertEqual("due", next_period["status"])
            self.assertTrue(next_period["should_run"])

    def test_check_period_marks_failed_period_due(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)
            configure(
                kb,
                harness="codex",
                timezone_name="Asia/Shanghai",
                environment="local",
                daily_schedule="工作日 20:30",
                weekly_schedule="周一 09:30",
                now=now,
            )
            lease = acquire_lease(
                kb,
                kind="weekly",
                period="2026-W30",
                owner="codex",
                lease_seconds=60,
                now=now,
            )
            complete_run(
                kb,
                token=lease["token"],
                run_status="failed",
                error_code="SOURCE_NETWORK_ERROR",
                now=now + timedelta(seconds=10),
            )
            result = check_period(
                kb,
                kind="weekly",
                period="2026-W30",
                now=now + timedelta(seconds=11),
            )
            self.assertEqual("due", result["status"])
            self.assertEqual("period_failed", result["reason"])

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

            configure(
                kb,
                harness="codex",
                timezone_name="Asia/Shanghai",
                environment="local",
                daily_schedule="工作日 20:30",
                weekly_schedule="周一 09:30",
            )
            checked = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "byteworker-cli.py"),
                    "report-automation",
                    "check",
                    "--kb",
                    str(kb),
                    "--kind",
                    "daily",
                    "--period",
                    "2026-07-30",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, checked.returncode, checked.stderr)
            checked_payload = json.loads(checked.stdout)
            self.assertEqual("success", checked_payload["status"])
            self.assertTrue(checked_payload["data"]["should_run"])


if __name__ == "__main__":
    unittest.main()
