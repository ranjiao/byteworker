import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_run_log import append_run_event, list_runs, show_run, tail_events
from dreaming_scheduler import (
    DreamingError,
    complete_run,
    configure,
    enable,
    heartbeat_run,
    register_harness,
    renew_lease,
    run_due,
    status,
    unregister_harness,
)


class DreamingRuntimeTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        return kb

    def enable_at(self, kb: Path, now: datetime, **kwargs):
        return enable(
            kb,
            harness="trae",
            timezone_name="Asia/Shanghai",
            acknowledge_machine_runtime=True,
            acknowledge_capability_tour=True,
            acknowledge_schedule=True,
            now=now,
            **kwargs,
        )

    def test_enable_requires_explicit_schedule_acknowledgement(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(DreamingError) as caught:
                enable(
                    kb,
                    harness="trae",
                    timezone_name="Asia/Shanghai",
                    acknowledge_machine_runtime=True,
                    acknowledge_capability_tour=True,
                )
            self.assertEqual("DREAMING_SCHEDULE_ACK_REQUIRED", caught.exception.code)

    def test_daily_schedule_preview_and_harness_truth(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            configured_at = datetime(2026, 8, 5, 0, 0, tzinfo=timezone.utc)
            configured = configure(
                kb,
                timezone_name="Asia/Shanghai",
                process_kind="daily_time",
                process_time="22:30",
                log_retention_days=45,
                lark_delivery_enabled=True,
                lark_recipient_id="ou_test",
                harness_wake_interval_minutes=120,
                harness_model="gpt-5.5",
                now=configured_at,
            )
            self.assertEqual(
                {"kind": "daily_time", "time": "22:30"},
                configured["jobs"]["process"]["schedule"],
            )
            self.assertEqual(
                {
                    "enabled": True,
                    "recipient_id": "ou_test",
                    "recipient_key": "ou_test",
                },
                configured["report_delivery"]["lark_bot"],
            )
            self.assertEqual(
                {"wake_interval_minutes": 120, "model": "gpt-5.5"},
                configured["harness_preferences"],
            )
            enabled = self.enable_at(kb, configured_at)
            self.assertFalse(enabled["operational"])
            self.assertEqual("pending", enabled["harness"]["status"])
            self.assertFalse(enabled["requires_schedule_acknowledgement"])
            self.assertEqual(
                "2026-08-05T14:30:00+00:00",
                enabled["jobs"]["process"]["next_due_at"],
            )
            installed = register_harness(
                kb,
                task_id="task-dreaming",
                now=configured_at + timedelta(minutes=1),
            )
            self.assertTrue(installed["operational"])
            self.assertEqual(45, installed["logging"]["retention_days"])
            self.assertEqual(
                "gpt-5.5",
                installed["harness_preferences"]["model"],
            )
            removed = unregister_harness(
                kb,
                now=configured_at + timedelta(minutes=2),
            )
            self.assertFalse(removed["operational"])

    def test_interval_and_every_n_days_process_schedules(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            hourly_kb = self.make_kb(root / "hourly")
            enabled_at = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)
            self.enable_at(
                hourly_kb,
                enabled_at,
                process_kind="interval",
                process_interval_minutes=60,
            )
            self.assertEqual(
                "idle",
                run_due(
                    hourly_kb,
                    owner="manual",
                    now=enabled_at + timedelta(minutes=59),
                )["status"],
            )
            self.assertEqual(
                "process",
                run_due(
                    hourly_kb,
                    owner="manual",
                    now=enabled_at + timedelta(minutes=60),
                )["job"],
            )

            every_kb = self.make_kb(root / "every")
            self.enable_at(
                every_kb,
                enabled_at,
                process_kind="every_n_days",
                process_every_days=3,
                process_time="22:00",
            )
            due_at = datetime(2026, 8, 5, 14, 0, tzinfo=timezone.utc)
            leased = run_due(every_kb, owner="manual", now=due_at)
            self.assertEqual("process", leased["job"])
            complete_run(
                every_kb,
                token=leased["lease"]["token"],
                run_status="success",
                now=due_at + timedelta(minutes=5),
            )
            next_status = status(
                every_kb,
                now=due_at + timedelta(minutes=6),
            )
            self.assertEqual(
                "2026-08-08T14:00:00+00:00",
                next_status["jobs"]["process"]["next_due_at"],
            )

    def test_job_preferences_survive_disable_and_reenable(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)
            configure(
                kb,
                timezone_name="Asia/Shanghai",
                morning_enabled=False,
                maintenance_enabled=False,
                recovery_enabled=True,
                now=enabled_at,
            )
            first = self.enable_at(kb, enabled_at)
            self.assertFalse(first["jobs"]["morning"]["enabled"])
            self.assertFalse(first["jobs"]["maintenance"]["enabled"])
            self.assertTrue(first["jobs"]["recovery"]["enabled"])
            from dreaming_scheduler import disable

            disabled = disable(kb, now=enabled_at + timedelta(minutes=1))
            self.assertFalse(disabled["jobs"]["recovery"]["enabled"])
            self.assertTrue(
                disabled["jobs"]["recovery"]["configured_enabled"]
            )
            restored = self.enable_at(kb, enabled_at + timedelta(minutes=2))
            self.assertFalse(restored["jobs"]["morning"]["enabled"])
            self.assertFalse(restored["jobs"]["maintenance"]["enabled"])
            self.assertTrue(restored["jobs"]["recovery"]["enabled"])

    def test_run_log_lifecycle_heartbeat_and_queries(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            enabled_at = datetime(2026, 8, 5, 1, 0, tzinfo=timezone.utc)
            self.enable_at(kb, enabled_at)
            register_harness(
                kb,
                task_id="host-task",
                now=enabled_at + timedelta(minutes=1),
            )
            due_at = enabled_at + timedelta(hours=2)
            leased = run_due(kb, owner="host-task", now=due_at)
            run_id = leased["lease"]["run_id"]
            heartbeat = heartbeat_run(
                kb,
                token=leased["lease"]["token"],
                stage="analysis",
                detail_code="FINDING_BUNDLE",
                progress_current=4,
                progress_total=10,
                now=due_at + timedelta(minutes=1),
            )
            self.assertEqual(run_id, heartbeat["run_id"])
            renew_lease(
                kb,
                token=leased["lease"]["token"],
                now=due_at + timedelta(minutes=2),
            )
            completed = complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                item_count=710,
                finding_count=5,
                gap_count=0,
                now=due_at + timedelta(minutes=3),
            )
            self.assertEqual(run_id, completed["run_id"])
            summary = list_runs(kb)
            self.assertEqual(1, summary["count"])
            self.assertEqual("success", summary["runs"][0]["status"])
            shown = show_run(kb, run_id=run_id)
            self.assertEqual(4, shown["event_count"])
            self.assertEqual(
                ["leased", "heartbeat", "renewed", "completed"],
                [event["event"] for event in shown["events"]],
            )
            self.assertEqual(
                710,
                shown["events"][-1]["metrics"]["item_count"],
            )
            self.assertEqual(2, tail_events(kb, limit=2)["returned"])
            current = status(kb, now=due_at + timedelta(minutes=4))
            self.assertEqual(
                due_at.isoformat(),
                current["harness"]["last_tick_at"],
            )

    def test_log_retention_prunes_old_daily_files(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            first = datetime(2026, 8, 1, tzinfo=timezone.utc)
            second = datetime(2026, 8, 5, tzinfo=timezone.utc)
            common = {
                "event": "leased",
                "job": "process",
                "period": "period",
                "owner": "host",
                "epoch": 1,
                "stage": "scheduled",
                "status": "running",
                "retention_days": 1,
            }
            append_run_event(kb, run_id="DR-old", now=first, **common)
            append_run_event(kb, run_id="DR-new", now=second, **common)
            result = list_runs(kb)
            self.assertEqual(["DR-new"], [item["run_id"] for item in result["runs"]])

    def test_log_rejects_free_text_detail_code(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(DreamingError) as caught:
                append_run_event(
                    kb,
                    run_id="DR-test",
                    event="heartbeat",
                    job="process",
                    period="period",
                    owner="host",
                    epoch=1,
                    stage="analysis",
                    status="running",
                    detail_code="正在分析某个群",
                )
            self.assertEqual("DREAMING_RUN_LOG_INVALID", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
