from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_reports import (  # noqa: E402
    complete_delivery,
    enqueue_delivery,
    prepare_report_packet,
    refresh_report_dependencies,
    report_dependency,
    report_migration_readiness,
    report_window,
)
from dreaming_scheduler import complete_run, enable, run_due  # noqa: E402
from dreaming_state import (  # noqa: E402
    DreamingError,
    load_state_unlocked,
    save_state_unlocked,
    state_lock,
)
from source_profile_contract import SourceProfileError  # noqa: E402


class DreamingReportTests(unittest.TestCase):
    def make_kb(self, root: Path, now: datetime) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        enable(
            kb,
            harness="test",
            timezone_name="Asia/Shanghai",
            acknowledge_machine_runtime=True,
            acknowledge_capability_tour=True,
            now=now,
        )
        return kb

    def set_cursor(self, kb: Path, key: str, through: str, now: datetime) -> None:
        with state_lock(kb):
            state = load_state_unlocked(kb, now)
            state["cursors"][key] = {
                "through": through,
                "committed_batch_id": "EB-test",
                "updated_at": through,
            }
            save_state_unlocked(kb, state)

    def test_report_windows(self):
        daily = report_window("daily", "2026-08-04", "Asia/Shanghai")
        morning = report_window("morning", "2026-08-04", "Asia/Shanghai")
        weekly = report_window("weekly", "2026-W32", "Asia/Shanghai")
        self.assertEqual(24 * 3600, (
            datetime.fromisoformat(daily["end"])
            - datetime.fromisoformat(daily["start"])
        ).total_seconds())
        self.assertLess(morning["start"], morning["end"])
        self.assertEqual(7 * 24 * 3600, (
            datetime.fromisoformat(weekly["end"])
            - datetime.fromisoformat(weekly["start"])
        ).total_seconds())
        current = report_window(
            "daily",
            "2026-08-04",
            "Asia/Shanghai",
            as_of=datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            datetime(2026, 8, 4, 12, 30, tzinfo=timezone.utc),
            datetime.fromisoformat(current["end"]),
        )

    def test_missing_cursor_blocks_and_scheduler_leases_catchup(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)  # 07:00 local
            kb = self.make_kb(Path(temporary), enabled_at)
            set_im_grant(
                kb,
                mode="monitored",
                persist_finding=False,
                acknowledge_all_visible=False,
                now=enabled_at,
            )
            due_at = enabled_at + timedelta(hours=2)  # 09:00 local
            leased = run_due(kb, owner="host", now=due_at)
            self.assertEqual("process", leased["job"])
            self.assertTrue(leased["period"].startswith("catchup:"))
            self.assertEqual("im", leased["dependency"]["source"])

            complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                now=due_at + timedelta(minutes=1),
            )
            blocker_end = leased["dependency"]["end"]
            self.set_cursor(kb, "im:monitored", blocker_end, due_at)
            self.assertEqual(1, refresh_report_dependencies(kb)["cleared"])
            next_run = run_due(
                kb,
                owner="host",
                now=due_at + timedelta(minutes=2),
            )
            self.assertEqual("morning", next_run["job"])

    def test_prepare_packet_is_private_and_outbox_is_separate(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            result = prepare_report_packet(
                kb,
                kind="morning",
                period="2026-08-04",
                now=now,
            )
            packet = kb / result["packet_path"]
            self.assertTrue(packet.is_file())
            self.assertEqual(0o600, stat.S_IMODE(packet.stat().st_mode))
            queued = enqueue_delivery(
                kb,
                kind="morning",
                period="2026-08-04",
                report_path="reports/morning/2026-08-04.md",
                commit="abc",
                now=now,
            )
            delivered = complete_delivery(
                kb,
                outbox_id=queued["outbox_id"],
                delivery_id="delivery-1",
                now=now,
            )
            self.assertEqual("delivered", delivered["status"])

    def test_unsupported_routine_source_blocks_owner_migration(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            profile = {
                "source_type": "meego",
                "routine": {"enabled": True},
            }
            with mock.patch("dreaming_reports.list_profiles", return_value=[profile]):
                readiness = report_migration_readiness(kb)
            self.assertFalse(readiness["ready"])

            with mock.patch(
                "dreaming_reports.list_profiles",
                side_effect=SourceProfileError(
                    "SOURCE_PROFILE_INVALID",
                    "broken",
                ),
            ):
                invalid = report_migration_readiness(kb)
            self.assertFalse(invalid["ready"])

    def test_report_dependency_is_covered_when_im_off(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            result = report_dependency(
                kb,
                kind="daily",
                period=date(2026, 8, 4).isoformat(),
            )
            self.assertEqual("covered", result["status"])


if __name__ == "__main__":
    unittest.main()
