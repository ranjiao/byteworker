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

from dreaming_scheduler import (  # noqa: E402
    STATE_SCHEMA,
    DreamingError,
    complete_run,
    disable,
    enable,
    run_due,
    renew_lease,
    retry_job,
    set_report_management,
    state_path,
    status,
)
from dreaming_state import atomic_write_json  # noqa: E402


class DreamingSchedulerTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        (kb / ".git" / "info" / "exclude").write_text(
            "# local excludes\n",
            encoding="utf-8",
        )
        return kb

    def enable_at(self, kb: Path, now: datetime) -> dict:
        return enable(
            kb,
            harness="trae",
            timezone_name="Asia/Shanghai",
            acknowledge_machine_runtime=True,
            acknowledge_capability_tour=True,
            now=now,
        )

    def test_missing_state_is_disabled_and_does_not_write(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            payload = status(kb, now=datetime(2026, 8, 3, tzinfo=timezone.utc))
            self.assertFalse(payload["enabled"])
            self.assertTrue(payload["requires_explicit_enable"])
            self.assertFalse(payload["machine_runtime_required"])
            self.assertTrue(payload["requires_capability_tour"])
            self.assertIn("开机", payload["runtime_notice"])
            self.assertFalse(state_path(kb).exists())
            self.assertFalse(state_path(kb).parent.exists())

    def test_enable_requires_runtime_acknowledgement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(DreamingError) as caught:
                enable(
                    kb,
                    harness="trae",
                    timezone_name="Asia/Shanghai",
                    acknowledge_machine_runtime=False,
                    acknowledge_capability_tour=True,
                )
            self.assertEqual("DREAMING_RUNTIME_ACK_REQUIRED", caught.exception.code)
            self.assertFalse(state_path(kb).exists())

    def test_enable_requires_capability_tour_acknowledgement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(DreamingError) as caught:
                enable(
                    kb,
                    harness="trae",
                    timezone_name="Asia/Shanghai",
                    acknowledge_machine_runtime=True,
                    acknowledge_capability_tour=False,
                )
            self.assertEqual(
                "DREAMING_CAPABILITY_TOUR_REQUIRED",
                caught.exception.code,
            )
            self.assertFalse(state_path(kb).exists())

    def test_enable_is_local_and_does_not_take_over_reports(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            payload = self.enable_at(kb, now)
            self.assertTrue(payload["enabled"])
            self.assertEqual("byteworker-dreaming/v2", STATE_SCHEMA)
            self.assertEqual(STATE_SCHEMA, payload["schema_version"])
            self.assertEqual("local", payload["environment"])
            self.assertTrue(payload["machine_runtime_required"])
            self.assertTrue(payload["jobs"]["process"]["enabled"])
            self.assertTrue(payload["jobs"]["morning"]["enabled"])
            self.assertTrue(payload["jobs"]["recovery"]["enabled"])
            self.assertTrue(payload["jobs"]["maintenance"]["enabled"])
            self.assertFalse(payload["jobs"]["daily"]["enabled"])
            self.assertFalse(payload["jobs"]["weekly"]["enabled"])
            self.assertFalse(payload["requires_capability_tour"])
            self.assertEqual(
                "byteworker-dreaming-tour/v1",
                payload["capability_tour_version"],
            )
            self.assertIsNotNone(payload["capability_tour_acknowledged_at"])
            self.assertFalse(payload["manage_reports"])
            self.assertEqual("off", payload["grants"]["im"]["mode"])
            self.assertFalse(payload["grants"]["im"]["persist_finding"])
            self.assertEqual({}, payload["runs"])
            self.assertEqual({}, payload["cursors"])
            self.assertEqual({}, payload["gaps"])

    def test_reenable_preserves_v2_operational_slots(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            first = self.enable_at(kb, enabled_at)
            first["grants"]["revision"] = 4
            first["grants"]["im"]["mode"] = "monitored"
            first["cursors"] = {"chat:oc_test": {"through": "message:om_1"}}
            first["runs"] = {"RUN-1": {"status": "success"}}
            atomic_write_json(state_path(kb), first)

            second = self.enable_at(kb, enabled_at + timedelta(minutes=1))

            self.assertEqual(4, second["grants"]["revision"])
            self.assertEqual("monitored", second["grants"]["im"]["mode"])
            self.assertEqual(first["cursors"], second["cursors"])
            self.assertEqual(first["runs"], second["runs"])
            self.assertGreater(
                second["state_revision"],
                first["state_revision"],
            )
            self.assertIn(
                "/state/",
                (kb / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )

    def test_invalid_timezone_or_environment_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            for timezone_name, environment in (
                ("Invalid/Zone", "local"),
                ("Asia/Shanghai", "cloud"),
            ):
                with self.subTest(timezone=timezone_name, environment=environment):
                    with self.assertRaises(DreamingError) as caught:
                        enable(
                            kb,
                            harness="trae",
                            timezone_name=timezone_name,
                            environment=environment,
                            acknowledge_machine_runtime=True,
                            acknowledge_capability_tour=True,
                        )
                    self.assertEqual("DREAMING_CONFIG_INVALID", caught.exception.code)

    def test_disabled_run_due_has_no_side_effects(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            payload = run_due(
                kb,
                owner="host-task",
                now=datetime(2026, 8, 3, tzinfo=timezone.utc),
            )
            self.assertEqual("disabled", payload["status"])
            self.assertIsNone(payload["lease"])
            self.assertFalse(state_path(kb).exists())

    def test_due_job_lease_completion_and_interval(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)

            idle = run_due(kb, owner="host-task", now=enabled_at)
            self.assertEqual("idle", idle["status"])

            due_at = enabled_at + timedelta(hours=2)
            leased = run_due(kb, owner="host-task", now=due_at)
            self.assertEqual("leased", leased["status"])
            self.assertEqual("process", leased["job"])
            self.assertIn("token", leased["lease"])

            busy = run_due(
                kb,
                owner="other-task",
                now=due_at + timedelta(seconds=1),
            )
            self.assertEqual("busy", busy["status"])
            self.assertNotIn("token", busy["active_lease"])

            completed = complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                coverage_checkpoint="sources-through:2026-08-03T03:00Z",
                now=due_at + timedelta(minutes=5),
            )
            self.assertEqual("success", completed["status"])
            self.assertEqual(
                "sources-through:2026-08-03T03:00Z",
                completed["coverage_checkpoint"],
            )
            not_due = run_due(
                kb,
                owner="host-task",
                now=due_at + timedelta(hours=1),
            )
            self.assertEqual("idle", not_due["status"])

    def test_maintenance_runs_on_schedule_and_waits_for_user_decision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 18, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)  # 02:00 Asia/Shanghai
            due_at = enabled_at + timedelta(hours=1, minutes=30)

            leased = run_due(kb, owner="host", now=due_at)

            self.assertEqual("maintenance", leased["job"])
            completed = complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="partial",
                error_code="DOCTOR_USER_DECISION_REQUIRED",
                coverage_checkpoint="doctor:error=1,warning=2,commit=none",
                now=due_at + timedelta(minutes=5),
            )
            self.assertEqual("partial", completed["status"])
            maintenance = status(kb, now=due_at + timedelta(minutes=5))["jobs"][
                "maintenance"
            ]
            self.assertEqual(
                "DOCTOR_USER_DECISION_REQUIRED",
                maintenance["waiting_for_user"],
            )
            self.assertIsNone(maintenance["next_attempt_at"])

    def test_expired_lease_is_fenced_and_replaced(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            due_at = enabled_at + timedelta(hours=2)
            first = run_due(
                kb,
                owner="first",
                lease_seconds=60,
                now=due_at,
            )
            second = run_due(
                kb,
                owner="second",
                lease_seconds=60,
                now=due_at + timedelta(seconds=61),
            )
            self.assertEqual("leased", second["status"])
            self.assertNotEqual(first["lease"]["token"], second["lease"]["token"])
            self.assertGreater(second["lease"]["epoch"], first["lease"]["epoch"])
            with self.assertRaises(DreamingError) as caught:
                complete_run(
                    kb,
                    token=first["lease"]["token"],
                    run_status="success",
                    now=due_at + timedelta(seconds=62),
                )
            self.assertEqual("DREAMING_LEASE_MISMATCH", caught.exception.code)

    def test_failed_process_backs_off_and_does_not_starve_recovery(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            process = run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=2),
            )
            failed_at = enabled_at + timedelta(hours=4)
            complete_run(
                kb,
                token=process["lease"]["token"],
                run_status="failed",
                error_code="NETWORK_TIMEOUT",
                now=failed_at,
            )

            recovery = run_due(
                kb,
                owner="host",
                now=failed_at + timedelta(minutes=1),
            )

            self.assertEqual("recovery", recovery["job"])
            process_state = status(kb, now=failed_at)["jobs"]["process"]
            self.assertEqual(1, process_state["consecutive_failures"])
            self.assertEqual(
                failed_at + timedelta(minutes=5),
                datetime.fromisoformat(process_state["next_attempt_at"]),
            )

    def test_human_blocking_failure_waits_for_user(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            process = run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=2),
            )
            complete_run(
                kb,
                token=process["lease"]["token"],
                run_status="failed",
                error_code="SOURCE_AUTH_REQUIRED",
                now=enabled_at + timedelta(hours=2, minutes=1),
            )
            value = status(kb)["jobs"]["process"]
            self.assertEqual("SOURCE_AUTH_REQUIRED", value["waiting_for_user"])
            self.assertIsNone(value["next_attempt_at"])
            receipt = retry_job(
                kb,
                job_name="process",
                now=enabled_at + timedelta(hours=2, minutes=2),
            )
            self.assertEqual("retry_enabled", receipt["status"])
            self.assertIsNone(status(kb)["jobs"]["process"]["waiting_for_user"])

    def test_blocked_job_is_not_selected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            value = self.enable_at(kb, enabled_at)
            value["jobs"]["process"]["blocked_by"] = ["source:auth"]
            atomic_write_json(state_path(kb), value)

            leased = run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=4),
            )

            self.assertEqual("recovery", leased["job"])

    def test_renew_extends_only_live_matching_lease(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            due_at = enabled_at + timedelta(hours=2)
            leased = run_due(
                kb,
                owner="host",
                lease_seconds=60,
                now=due_at,
            )
            renewed = renew_lease(
                kb,
                token=leased["lease"]["token"],
                lease_seconds=120,
                now=due_at + timedelta(seconds=30),
            )
            self.assertEqual(
                due_at + timedelta(seconds=150),
                datetime.fromisoformat(renewed["expires_at"]),
            )
            with self.assertRaises(DreamingError) as caught:
                renew_lease(
                    kb,
                    token="wrong",
                    now=due_at + timedelta(seconds=31),
                )
            self.assertEqual("DREAMING_LEASE_MISMATCH", caught.exception.code)

    def test_partial_or_failed_run_requires_error_code(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            leased = run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=2),
            )
            for run_status in ("partial", "failed"):
                with self.subTest(run_status=run_status):
                    with self.assertRaises(DreamingError) as caught:
                        complete_run(
                            kb,
                            token=leased["lease"]["token"],
                            run_status=run_status,
                        )
                    self.assertEqual(
                        "DREAMING_RUN_RESULT_INVALID",
                        caught.exception.code,
                    )
            failed = complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="failed",
                error_code="SOURCE_AUTH_REQUIRED",
            )
            self.assertEqual("SOURCE_AUTH_REQUIRED", failed["error_code"])

    def test_disable_rejects_active_job_then_preserves_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            leased = run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=2),
            )
            with self.assertRaises(DreamingError) as caught:
                disable(kb, now=enabled_at + timedelta(hours=2, minutes=1))
            self.assertEqual("DREAMING_BUSY", caught.exception.code)
            complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                now=enabled_at + timedelta(hours=2, minutes=2),
            )
            payload = disable(
                kb,
                now=enabled_at + timedelta(hours=2, minutes=3),
            )
            self.assertFalse(payload["enabled"])
            self.assertIsNotNone(payload["jobs"]["process"]["last_success"])

    def test_report_management_refuses_existing_owner_without_mutating_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            report_state = kb / "state" / "report_automation.json"
            report_state.parent.mkdir(parents=True, exist_ok=True)
            original = {
                "schema_version": "byteworker-report-automation/v1",
                "decision": "configured",
                "daily": {"enabled": True},
                "weekly": {"enabled": True},
            }
            report_state.write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )
            with self.assertRaises(DreamingError) as caught:
                set_report_management(
                    kb,
                    enabled=True,
                    acknowledge_owner_released=True,
                    now=enabled_at + timedelta(minutes=1),
                )
            self.assertEqual(
                "DREAMING_REPORT_OWNER_CONFLICT",
                caught.exception.code,
            )
            self.assertEqual(
                original,
                json.loads(report_state.read_text(encoding="utf-8")),
            )
            self.assertFalse(status(kb)["manage_reports"])

    def test_active_job_blocks_reconfiguration_and_report_migration(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            run_due(
                kb,
                owner="host",
                now=enabled_at + timedelta(hours=2),
            )
            with self.assertRaises(DreamingError) as caught:
                self.enable_at(
                    kb,
                    enabled_at + timedelta(hours=2, minutes=1),
                )
            self.assertEqual("DREAMING_BUSY", caught.exception.code)
            with self.assertRaises(DreamingError) as caught:
                set_report_management(
                    kb,
                    enabled=True,
                    acknowledge_owner_released=True,
                    now=enabled_at + timedelta(hours=2, minutes=1),
                )
            self.assertEqual("DREAMING_BUSY", caught.exception.code)

    def test_report_management_requires_ack_and_can_be_enabled_without_owner(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            with self.assertRaises(DreamingError) as caught:
                set_report_management(
                    kb,
                    enabled=True,
                    acknowledge_owner_released=False,
                    now=enabled_at + timedelta(minutes=1),
                )
            self.assertEqual(
                "DREAMING_REPORT_MIGRATION_ACK_REQUIRED",
                caught.exception.code,
            )
            payload = set_report_management(
                kb,
                enabled=True,
                acknowledge_owner_released=True,
                now=enabled_at + timedelta(minutes=2),
            )
            self.assertTrue(payload["manage_reports"])
            self.assertTrue(payload["jobs"]["daily"]["enabled"])
            self.assertTrue(payload["jobs"]["weekly"]["enabled"])
            payload = set_report_management(
                kb,
                enabled=False,
                acknowledge_owner_released=False,
                now=enabled_at + timedelta(minutes=3),
            )
            self.assertFalse(payload["manage_reports"])

    def test_report_management_accepts_explicitly_released_legacy_owner(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            report_state = kb / "state" / "report_automation.json"
            report_state.parent.mkdir(parents=True, exist_ok=True)
            report_state.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-report-automation/v1",
                        "decision": "configured",
                        "scheduler_owner": "released-to-dreaming",
                        "daily": {
                            "enabled": False,
                            "last_success": {
                                "status": "success",
                                "period": "2026-08-01",
                                "report_path": "reports/daily/2026-08-01.md",
                            },
                        },
                        "weekly": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            payload = set_report_management(
                kb,
                enabled=True,
                acknowledge_owner_released=True,
                now=enabled_at + timedelta(minutes=1),
            )
            self.assertTrue(payload["manage_reports"])
            self.assertEqual("dreaming", payload["report_owner"]["owner"])
            self.assertEqual(
                "released-to-dreaming",
                payload["report_owner"]["legacy_snapshot"]["scheduler_owner"],
            )
            self.assertEqual(
                "2026-08-01",
                payload["jobs"]["daily"]["last_success"]["period"],
            )

    def test_cli_and_machine_facade_report_disabled_by_default(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            direct = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "status",
                    "--kb",
                    str(kb),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, direct.returncode, direct.stderr)
            self.assertFalse(json.loads(direct.stdout)["enabled"])

            facade = subprocess.run(
                [
                    str(ROOT / "bin" / "byteworker"),
                    "dreaming",
                    "status",
                    "--kb",
                    str(kb),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, facade.returncode, facade.stderr)
            payload = json.loads(facade.stdout)
            self.assertEqual("success", payload["status"])
            self.assertFalse(payload["data"]["enabled"])

    def test_cli_enable_requires_tour_flag(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "enable",
                    "--kb",
                    str(kb),
                    "--harness",
                    "trae",
                    "--timezone",
                    "Asia/Shanghai",
                    "--acknowledge-machine-runtime",
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(2, completed.returncode)
            self.assertEqual(
                "DREAMING_CAPABILITY_TOUR_REQUIRED",
                json.loads(completed.stdout)["error"]["code"],
            )
            self.assertFalse(state_path(kb).exists())


if __name__ == "__main__":
    unittest.main()
