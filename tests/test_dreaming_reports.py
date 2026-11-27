from datetime import date, datetime, timedelta, timezone
import json
import os
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
    apply_manual_source_gaps,
)
from dreaming_report_bundle import render_report_bundle  # noqa: E402
from dreaming_report_completion import complete_report_run  # noqa: E402
from dreaming_delivery_lark import (  # noqa: E402
    deliver_lark_bot_summary,
    format_lark_summary_message,
)
from dreaming_scheduler import complete_run, configure, enable, run_due, start_manual_report  # noqa: E402
from dreaming_state import (  # noqa: E402
    DreamingError,
    load_state_unlocked,
    save_state_unlocked,
    state_lock,
)
import dreaming_report_bundle  # noqa: E402


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
            acknowledge_schedule=True,
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

    def report_document(self) -> dict:
        summary = "甲乙丙丁。" * 64
        self.assertGreaterEqual(len(summary), 300)
        self.assertLessEqual(len(summary), 500)
        return {
            "schema_version": "byteworker-report-document/v1",
            "kind": "morning",
            "period": "2026-08-04",
            "title": "晨报 · 2026-08-04",
            "generated_at": "2026-08-04 10:00",
            "window": {
                "start": "2026-08-03 20:30",
                "end": "2026-08-04 10:00",
                "timezone": "Asia/Shanghai",
            },
            "coverage": {
                "status": "partial",
                "notes": ["全部可见会话为尽力扫描。"],
            },
            "message_summary": summary,
            "sections": {
                "highlights": [
                    {
                        "title": "<script>alert(1)</script>示例标题甲",
                        "detail": "示例条目正文。",
                        "severity": "attention",
                        "source_refs": ["S1"],
                    }
                ],
                "changes": [],
                "risks": [],
                "confirmations": [],
            },
            "sources": [
                {
                    "id": "S1",
                    "title": "示例来源甲",
                    "type": "飞书群聊",
                    "locator": "chat_id=oc_test",
                    "observed_at": "2026-08-04 09:00",
                    "confidence": "高",
                }
            ],
            "manual_notes": "",
        }

    def manual_kb(self, root, now):
        kb = self.make_kb(root, now)
        with state_lock(kb):
            state = load_state_unlocked(kb, now)
            state["manage_reports"] = True
            state["jobs"]["daily"]["enabled"] = True
            state["jobs"]["daily"]["pending_periods"] = {"2026-08-04": now.isoformat()}
            state["report_delivery"]["lark_bot"].update(enabled=True, recipient_id="ou_test")
            save_state_unlocked(kb, state)
        return kb

    def test_manual_gap_policy_keeps_im_and_invalid_profiles_blocking(self):
        dependency = {
            "status": "blocked", "partial_reasons": [],
            "blockers": [
                {"source": "routine_digest", "source_key": "one"},
                {"source": "routine_digest", "source_key": "two"},
                {"source": "im"},
                {"source": "routine_digest", "error_code": "SOURCE_PROFILE_INVALID"},
            ],
        }
        lease = {"manual_report": {"accepted_source_gaps": ["one"], "gap_note": "日期缺失。"}}
        result = apply_manual_source_gaps(dependency, lease)
        self.assertEqual("blocked", result["status"])
        self.assertEqual(3, len(result["blockers"]))
        self.assertEqual("one", result["partial_reasons"][0]["source_key"])
        self.assertEqual(4, len(dependency["blockers"]))
        self.assertEqual(dependency, apply_manual_source_gaps(dependency, {}))

    @mock.patch("dreaming_reports.routine_inventory")
    def test_manual_report_registers_partial_archive_without_delivery(self, inventory):
        inventory.return_value = [{
            "source_key": "synthetic-source", "source_type": "feishu_doc",
            "profile_revision": "sha256:synthetic",
        }]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 5, 2, tzinfo=timezone.utc)
            kb = self.manual_kb(Path(temporary), now)
            with self.assertRaises(DreamingError) as caught:
                start_manual_report(kb, kind="daily", period="2026-08-04", owner="host", now=now)
            self.assertEqual("DREAMING_REPORT_BLOCKED", caught.exception.code)
            leased = start_manual_report(
                kb, kind="daily", period="2026-08-04", owner="host",
                accepted_source_gaps=["synthetic-source"], gap_note="来源未提供目标日期。",
                now=now,
            )
            prepared = prepare_report_packet(kb, kind="daily", period="2026-08-04", now=now)
            self.assertEqual("partial", prepared["coverage"]["status"])
            self.assertEqual("blocked", report_dependency(
                kb, kind="daily", period="2026-08-03", now=now,
            )["status"])
            self.assertEqual("blocked", report_dependency(
                kb, kind="daily", period="2026-08-04", now=now + timedelta(hours=3),
            )["status"])
            document = self.report_document()
            document.update(kind="daily", title="日报 · 2026-08-04")
            document["coverage"] = {"status": "covered", "notes": []}
            with mock.patch("dreaming_report_completion._deliver_if_enabled") as delivery:
                completed = complete_report_run(
                    kb, token=leased["lease"]["token"], document=document, now=now,
                )
                delivery.assert_not_called()
            self.assertEqual("manual_delivery_not_requested", completed["delivery"]["reason"])
            manifest = json.loads((kb / completed["rendered"]["manifest_path"]).read_text())
            rendered_document = json.loads(Path(manifest["artifacts"]["document"]["absolute_path"]).read_text())
            self.assertEqual("partial", rendered_document["coverage"]["status"])
            self.assertIn("来源未提供目标日期。", rendered_document["coverage"]["notes"])
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            self.assertNotIn("2026-08-04", state["jobs"]["daily"]["pending_periods"])
            self.assertEqual("2026-08-04", state["jobs"]["daily"]["last_success"]["period"])
            self.assertIsNone(state["active_lease"])
            self.assertEqual({}, state["source_checkpoints"])
            self.assertEqual({}, state["outbox"])
            self.assertEqual("covered", document["coverage"]["status"])

    def test_manual_report_rejects_invalid_claims_and_busy_lease(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime(2026, 8, 5, 2, tzinfo=timezone.utc)
            kb = self.manual_kb(Path(temporary), now)
            for extra in (
                {"accepted_source_gaps": ["unknown"], "gap_note": "缺口"},
                {"accepted_source_gaps": ["unknown"]},
                {"period": "2026-09-01"},
            ):
                arguments = dict(kind="daily", period="2026-08-04", owner="host", now=now)
                arguments.update(extra)
                with self.assertRaises(DreamingError):
                    start_manual_report(kb, **arguments)
            leased = start_manual_report(kb, kind="daily", period="2026-08-04", owner="host", now=now)
            with self.assertRaises(DreamingError) as caught:
                start_manual_report(kb, kind="daily", period="2026-08-04", owner="host2", now=now)
            self.assertEqual("DREAMING_LEASE_BUSY", caught.exception.code)
            with state_lock(kb):
                self.assertEqual(leased["lease"], load_state_unlocked(kb, now)["active_lease"])

    def test_manual_report_rechecks_coverage_before_publish(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime(2026, 8, 5, 2, tzinfo=timezone.utc)
            kb = self.manual_kb(Path(temporary), now)
            leased = start_manual_report(kb, kind="daily", period="2026-08-04", owner="host", now=now)
            set_im_grant(kb, mode="monitored", persist_finding=True,
                         acknowledge_all_visible=False, now=now)
            document = self.report_document()
            document["kind"] = "daily"
            with self.assertRaises(DreamingError) as caught:
                complete_report_run(kb, token=leased["lease"]["token"], document=document, now=now)
            self.assertEqual("DREAMING_REPORT_BLOCKED", caught.exception.code)
            self.assertFalse((kb / "reports/daily/2026-08-04.md").exists())

    def test_report_windows(self):
        daily = report_window("daily", "2026-08-04", "Asia/Shanghai")
        morning = report_window("morning", "2026-08-04", "Asia/Shanghai")
        weekly = report_window("weekly", "2026-W32", "Asia/Shanghai")
        self.assertEqual(24 * 3600, (
            datetime.fromisoformat(daily["end"])
            - datetime.fromisoformat(daily["start"])
        ).total_seconds())
        self.assertEqual(
            datetime(2026, 8, 4, 2, tzinfo=timezone.utc),
            datetime.fromisoformat(morning["end"]),
        )
        self.assertEqual(
            13.5 * 3600,
            (
                datetime.fromisoformat(morning["end"])
                - datetime.fromisoformat(morning["start"])
            ).total_seconds(),
        )
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

            blocker_end = leased["dependency"]["end"]
            self.set_cursor(kb, "im:monitored", blocker_end, due_at)
            complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                now=due_at + timedelta(minutes=1),
            )
            self.assertEqual(1, refresh_report_dependencies(kb)["cleared"])
            next_run = run_due(
                kb,
                owner="host",
                now=due_at + timedelta(minutes=2),
            )
            self.assertEqual("morning", next_run["job"])

    @mock.patch("dreaming_reports.routine_inventory", return_value=[{
        "source_key": "synthetic", "source_type": "feishu_doc", "profile_revision": "synthetic",
    }])
    def test_dailies_publish_partial_before_regular_digest_and_weekly_backfill(self, inventory):
        with tempfile.TemporaryDirectory() as temporary:
            wake = datetime(2026, 11, 18, 12, 30, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), wake - timedelta(days=10))
            with state_lock(kb):
                state = load_state_unlocked(kb, wake)
                for name, job in state["jobs"].items():
                    job["enabled"] = name in {"process", "daily", "weekly", "recovery"}
                state["grants"]["im"]["mode"] = "monitored"
                original_cursor = {"through": (wake - timedelta(days=6)).isoformat()}
                state["cursors"]["im:monitored"] = original_cursor
                save_state_unlocked(kb, state)

            for day in ("2026-11-18", "2026-11-17"):
                report = run_due(kb, owner="host", now=wake)["lease"]
                self.assertEqual(("daily", day), (report["job"], report["period"]))
                prepared = prepare_report_packet(kb, kind="daily", period=day, now=wake)
                self.assertEqual("partial", prepared["coverage"]["status"])
                self.assertEqual({"im", "routine_digest"}, {
                    reason["source"] for reason in prepared["coverage"]["partial_reasons"]
                })
                self.assertEqual(0, prepared["finding_count"])
                document = {
                    **self.report_document(), "kind": "daily", "period": day,
                    "coverage": {"status": "covered", "notes": []},
                    "message_summary": "本期暂无可核实的进展。",
                    "sections": {}, "sources": [],
                }
                receipt = complete_report_run(kb, token=report["token"], document=document, now=wake)
                manifest = json.loads((kb / receipt["rendered"]["manifest_path"]).read_text())
                rendered = json.loads(Path(manifest["artifacts"]["document"]["absolute_path"]).read_text())
                self.assertEqual("partial", rendered["coverage"]["status"])
                self.assertEqual(2, len(rendered["coverage"]["notes"]))
                self.assertTrue((kb / receipt["run"]["artifact_path"]).is_file())

            with state_lock(kb):
                state = load_state_unlocked(kb, wake)
                self.assertEqual(original_cursor, state["cursors"]["im:monitored"])
                self.assertEqual({}, state["source_checkpoints"])
                self.assertNotIn("2026-11-18", state["jobs"]["daily"]["pending_periods"])
                self.assertNotIn("2026-11-17", state["jobs"]["daily"]["pending_periods"])
            self.assertEqual("blocked", report_dependency(kb, kind="weekly", period="2026-W46", now=wake)["status"])
            self.assertEqual("process", run_due(kb, owner="host", now=wake)["job"])

    def test_ordinary_digest_resumes_split_windows_after_interruption(self):
        from dreaming_collection import prepare_im_batch
        from dreaming_process import commit_finding_bundle

        wake = datetime(2026, 8, 13, 4, tzinfo=timezone.utc)
        initial = wake - timedelta(hours=4)
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary), wake - timedelta(days=1))
            with state_lock(kb):
                state = load_state_unlocked(kb, wake)
                for name, job in state["jobs"].items():
                    job["enabled"] = name == "process"
                state["grants"]["im"]["mode"] = "all_visible"
                state["cursors"]["im:discovery"] = {"through": initial.isoformat()}
                save_state_unlocked(kb, state)
            collector = mock.Mock()
            collector.principal.return_value = "user:test"
            clean = {"coverage": {"status": "best_effort", "gaps": []}, "messages": []}
            collector.collect_discovery.side_effect = [
                {**clean, "coverage": {"status": "best_effort", "gaps": [{"kind": "window_budget"}]}},
                clean, clean, clean,
            ]

            def collect(now, lease, start, end):
                with self.assertRaises(DreamingError) as blocked:
                    complete_run(kb, token=lease["token"], run_status="success", now=now)
                expected = {"start": start.isoformat(), "end": end.isoformat()}
                self.assertEqual("DREAMING_IM_INCOMPLETE", blocked.exception.code)
                self.assertEqual(expected, blocked.exception.details["window"])
                batch = prepare_im_batch(kb, collector=collector, now=now)
                self.assertEqual(expected, batch["window"])
                bundle = Path(temporary) / "bundle.json"
                bundle.write_text(json.dumps({
                    "schema_version": "byteworker-finding-bundle/v1", "batch_id": batch["batch_id"], "findings": [],
                }))
                commit_finding_bundle(kb, batch_id=batch["batch_id"], input_path=bundle, skill_root=ROOT, now=now)

            first = run_due(kb, owner="host", now=wake)["lease"]
            middle = initial + (wake - initial) / 2
            collect(wake, first, initial, wake)
            collect(wake, first, initial, middle)
            complete_run(kb, token=first["token"], run_status="partial", error_code="NETWORK_TIMEOUT", now=wake)
            later = wake + timedelta(minutes=6)
            second = run_due(kb, owner="host", now=later)["lease"]
            collect(later, second, middle, wake)
            collect(later, second, wake, later)
            self.assertEqual("covered", prepare_im_batch(kb, collector=collector, now=later)["stage"])
            self.assertEqual(4, collector.collect_discovery.call_count)
            complete_run(kb, token=second["token"], run_status="success", now=later)

    def test_first_recent_collection_preserves_older_unobserved_weeks(self):
        from dreaming_collection import plan_recent_im_window

        with tempfile.TemporaryDirectory() as temporary:
            wake = datetime(2026, 11, 18, 13, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), wake - timedelta(days=35))
            with state_lock(kb):
                state = load_state_unlocked(kb, wake)
                state["grants"]["im"]["mode"] = "monitored"
                recent_start = datetime(2026, 11, 16, 16, tzinfo=timezone.utc)
                window = plan_recent_im_window(state, recent_start, wake)
                self.assertEqual(recent_start.isoformat(), window["start"])
                state["cursors"]["im:monitored"] = {"through": window["end"]}
                save_state_unlocked(kb, state)
            old_week = report_dependency(kb, kind="weekly", period="2026-W43", now=wake)
            self.assertEqual(["im"], [item["source"] for item in old_week["blockers"]])

    def test_upgrade_finds_missing_yesterday_after_today_already_succeeded(self):
        with tempfile.TemporaryDirectory() as temporary:
            wake = datetime(2026, 11, 18, 13, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), wake - timedelta(days=8))
            with state_lock(kb):
                state = load_state_unlocked(kb, wake)
                for name, job in state["jobs"].items():
                    job["enabled"] = name == "daily"
                state["jobs"]["daily"]["last_success"] = {
                    "status": "success", "period": "2026-11-18", "finished_at": wake.isoformat(),
                }
                save_state_unlocked(kb, state)
            report = run_due(kb, owner="host", now=wake)["lease"]
            self.assertEqual(("daily", "2026-11-17"), (report["job"], report["period"]))

    def test_blocked_weekly_survives_new_iso_year_and_newer_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            first = datetime(2025, 12, 29, 2, tzinfo=timezone.utc)  # Monday 10:00 Shanghai
            kb = self.make_kb(Path(temporary), first - timedelta(days=4))
            with state_lock(kb):
                state = load_state_unlocked(kb, first)
                for name, job in state["jobs"].items():
                    job["enabled"] = name == "weekly"
                state["grants"]["im"]["mode"] = "monitored"
                save_state_unlocked(kb, state)
            self.assertEqual("idle", run_due(kb, owner="host", now=first)["status"])
            second = first + timedelta(days=7)
            self.set_cursor(kb, "im:monitored", second.isoformat(), second)
            for period in ("2026-W01", "2025-W52"):
                lease = run_due(kb, owner="host", now=second)["lease"]
                self.assertEqual(("weekly", period), (lease["job"], lease["period"]))
                receipt = complete_report_run(kb, token=lease["token"], now=second, document={
                    **self.report_document(), "kind": "weekly", "period": period,
                })
                self.assertTrue((kb / receipt["run"]["artifact_path"]).is_file())
            self.assertEqual("idle", run_due(kb, owner="host", now=second)["status"])

    def test_daily_packet_keeps_scheduled_cutoff_across_midnight(self):
        with tempfile.TemporaryDirectory() as temporary:
            cutoff = datetime(2026, 11, 17, 12, 30, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), cutoff - timedelta(hours=3))
            with state_lock(kb):
                state = load_state_unlocked(kb, cutoff)
                for name, job in state["jobs"].items():
                    job["enabled"] = name == "daily"
                state["grants"]["im"]["mode"] = "monitored"
                save_state_unlocked(kb, state)
            self.assertEqual("idle", run_due(kb, owner="host", now=cutoff - timedelta(seconds=1))["status"])
            wake = cutoff.replace(hour=15, minute=59)
            report = run_due(kb, owner="host", now=wake)
            self.assertEqual("daily", report["job"])
            wake += timedelta(minutes=2)
            prepared = prepare_report_packet(kb, kind="daily", period=report["period"], now=wake)
            packet = json.loads((kb / prepared["packet_path"]).read_text())
            self.assertEqual(cutoff, datetime.fromisoformat(packet["window"]["end"]))
            self.assertEqual("partial", packet["coverage"]["status"])
            document = {**self.report_document(), "kind": "daily", "period": report["period"]}
            receipt = complete_report_run(kb, token=report["lease"]["token"], document=document, now=wake)
            manifest = json.loads((kb / receipt["rendered"]["manifest_path"]).read_text())
            rendered = json.loads(Path(manifest["artifacts"]["document"]["absolute_path"]).read_text())
            self.assertEqual(cutoff, datetime.fromisoformat(rendered["window"]["end"]))

    def test_daily_stays_pending_on_transaction_rollback_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            cutoff = datetime(2026, 11, 17, 12, 30, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), cutoff - timedelta(hours=3))
            with state_lock(kb):
                state = load_state_unlocked(kb, cutoff)
                for name, job in state["jobs"].items():
                    job["enabled"] = name == "daily"
                state["jobs"]["process"]["waiting_for_user"] = "DIGEST_TXN_ROLLBACK_FAILED"
                save_state_unlocked(kb, state)
            observation = {}
            self.assertEqual("idle", run_due(kb, owner="host", now=cutoff, observation=observation)["status"])
            self.assertEqual("blocked_by_dependency", observation["jobs"]["daily"])
            with self.assertRaises(DreamingError) as caught:
                prepare_report_packet(kb, kind="daily", period="2026-11-17", now=cutoff)
            self.assertEqual("DIGEST_TXN_ROLLBACK_FAILED", caught.exception.details["blockers"][0]["error_code"])

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
            self.assertNotIn("todo.md", packet.read_text(encoding="utf-8"))
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
            lark = enqueue_delivery(kb, kind="morning", period="2026-08-04",
                                    report_path="reports/morning/2026-08-04.md", commit="abc",
                                    channel="lark_bot", artifact="summary", recipient_id="ou_test", now=now)
            with self.assertRaises(DreamingError) as caught:
                complete_delivery(kb, outbox_id=lark["outbox_id"], delivery_id="invented", now=now)
            self.assertEqual("DREAMING_DELIVERY_INVALID", caught.exception.code)

    def test_report_preview_and_invalid_lease_preserve_archive(self):
        with tempfile.TemporaryDirectory() as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            archive = kb / "reports/morning/2026-08-04.md"
            archive.parent.mkdir(parents=True)
            archive.write_text("original\n")
            with self.assertRaises(DreamingError) as caught:
                complete_report_run(kb, token="invalid", document=self.report_document(), now=now)
            self.assertEqual("DREAMING_LEASE_MISMATCH", caught.exception.code)
            preview = render_report_bundle(kb, document=self.report_document(), preview=True)
            self.assertFalse(preview["report_path"])
            self.assertTrue(Path(preview["html_path"]).is_file())
            self.assertEqual("original\n", archive.read_text())

    def test_render_bundle_is_host_neutral_and_escapes_html(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            document = self.report_document()
            document["sections"]["todos"] = [{"title": "Legacy personal task"}]
            rendered = render_report_bundle(kb, document=document)
            manifest = json.loads(
                (kb / rendered["manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["host_delivery"]["host_specific_api_required"])
            self.assertEqual("preview_or_file_link", manifest["host_delivery"]["html"])
            self.assertEqual(
                {"document", "summary", "markdown", "html"},
                set(manifest["artifacts"]),
            )
            html_text = Path(rendered["html_path"]).read_text(encoding="utf-8")
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_text)
            self.assertNotIn("<script>alert(1)</script>", html_text)
            self.assertNotIn("<script src", html_text.lower())
            self.assertNotIn("<link", html_text.lower())
            self.assertNotIn("<img", html_text.lower())
            self.assertIn("data-src", html_text.lower())
            self.assertNotIn("@import", html_text.lower())
            self.assertNotIn("http://", html_text.lower())
            self.assertNotIn("https://", html_text.lower())
            self.assertNotIn("{{TITLE}}", html_text)
            self.assertNotIn("你的 Todo", html_text)
            self.assertNotIn("Legacy personal task", html_text)
            report_json = Path(manifest["artifacts"]["document"]["absolute_path"])
            self.assertNotIn("todos", json.loads(report_json.read_text())["sections"])
            self.assertIn("byteworker dreaming", html_text.lower())
            self.assertIn("Daily Intel Brief", html_text)
            archive = kb / rendered["report_path"]
            self.assertTrue(archive.is_file())
            self.assertEqual("reports/morning/2026-08-04.md", rendered["report_path"])
            self.assertIn("# 晨报 · 2026-08-04", archive.read_text(encoding="utf-8"))
            for artifact in manifest["artifacts"].values():
                self.assertEqual(
                    0o600,
                    stat.S_IMODE(Path(artifact["absolute_path"]).stat().st_mode),
                )

    def test_render_bundle_preserves_existing_manual_notes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            archive = kb / "reports" / "morning" / "2026-08-04.md"
            archive.parent.mkdir(parents=True)
            archive.write_text(
                "# old\n\n## 手动补充 / 备注\n- 保留这条人工备注\n",
                encoding="utf-8",
            )

            render_report_bundle(kb, document=self.report_document())

            updated = archive.read_text(encoding="utf-8")
            self.assertIn("- 保留这条人工备注", updated)

    def test_weekly_bundle_uses_weekly_headings(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            document = self.report_document()
            document.update(kind="weekly", period="2026-W32", title="周报 · 2026-W32")
            rendered = render_report_bundle(kb, document=document, preview=True)
            for artifact in ("html_path", "markdown_path"):
                text = Path(rendered[artifact]).read_text(encoding="utf-8")
                self.assertIn("关键进展与对齐", text)
                self.assertIn("下周关注与待确认", text)
                self.assertNotIn("夜间重要变化", text)
            html_text = Path(rendered["html_path"]).read_text(encoding="utf-8")
            self.assertIn("本周要点", html_text)
            self.assertNotIn("今日要点", html_text)

    def test_render_bundle_does_not_follow_archive_symlink_for_manual_notes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(root, now)
            outside = root / "outside.md"
            outside.write_text(
                "# outside\n\n## 手动补充 / 备注\n- 不应读取\n",
                encoding="utf-8",
            )
            archive = kb / "reports" / "morning" / "2026-08-04.md"
            archive.parent.mkdir(parents=True)
            os.symlink(outside, archive)

            render_report_bundle(kb, document=self.report_document())

            self.assertEqual(
                "# outside\n\n## 手动补充 / 备注\n- 不应读取\n",
                outside.read_text(encoding="utf-8"),
            )
            self.assertFalse(archive.is_symlink())
            self.assertNotIn("- 不应读取", archive.read_text(encoding="utf-8"))

    def test_complete_report_records_artifact_and_skips_disabled_delivery(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 0, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            configure(
                kb,
                process_enabled=False,
                maintenance_enabled=False,
                recovery_enabled=False,
                morning_time="10:00",
                now=enabled_at,
            )
            due_at = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            leased = run_due(kb, owner="host", now=due_at)
            self.assertEqual("morning", leased["job"])
            archive = kb / "reports/morning/2026-08-04.md"
            # Older installations issued leases past noon. The upgraded publisher
            # must fence those too, even if no scheduler wake has happened yet.
            with state_lock(kb):
                state = load_state_unlocked(kb, due_at)
                state["active_lease"]["expires_at"] = (due_at + timedelta(hours=4)).isoformat()
                save_state_unlocked(kb, state)
            for document, attempted_at in (
                ({**self.report_document(), "period": "2026-08-05"}, due_at),
                (self.report_document(), due_at + timedelta(hours=2)),
            ):
                with self.assertRaises(DreamingError) as caught:
                    complete_report_run(kb, token=leased["lease"]["token"], document=document, now=attempted_at)
                self.assertEqual("DREAMING_LEASE_MISMATCH", caught.exception.code)
                self.assertFalse(archive.exists())
                self.assertFalse(archive.with_name("2026-08-05.md").exists())

            completed = complete_report_run(
                kb,
                token=leased["lease"]["token"],
                document=self.report_document(),
                item_count=1,
                finding_count=1,
                gap_count=0,
                now=due_at,
            )

            self.assertEqual("completed", completed["status"])
            self.assertEqual("skipped", completed["delivery"]["status"])
            self.assertEqual(completed["rendered"]["summary"], completed["run"]["summary"]["summary"])
            accounting = completed["run"]["summary"]["accounting"]
            self.assertEqual({"reports_written": 1}, accounting["kb_operations"])
            self.assertTrue(accounting["report_sources"])
            self.assertEqual(
                "reports/morning/2026-08-04.md",
                completed["run"]["artifact_path"],
            )
            with state_lock(kb):
                state = load_state_unlocked(kb, due_at)
            self.assertEqual({}, state["outbox"])

    @mock.patch("dreaming_delivery_lark.subprocess.run")
    def test_complete_report_delivers_when_lark_summary_enabled(self, run):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 0, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            configure(
                kb,
                process_enabled=False,
                maintenance_enabled=False,
                recovery_enabled=False,
                morning_time="10:00",
                lark_delivery_enabled=True,
                lark_recipient_id="ou_test",
                now=enabled_at,
            )
            due_at = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            leased = run_due(kb, owner="host", now=due_at)
            run.return_value = mock.Mock(
                returncode=0,
                stdout=json.dumps(
                    {"ok": True, "data": {"message_id": "om_report"}}
                ),
                stderr="",
            )

            completed = complete_report_run(
                kb,
                token=leased["lease"]["token"],
                document=self.report_document(),
                item_count=1,
                finding_count=1,
                gap_count=0,
                delivery_binary="lark-cli",
                now=due_at,
            )

            self.assertEqual("delivered", completed["delivery"]["status"])
            self.assertEqual("om_report", completed["delivery"]["delivery_id"])
            command = run.call_args.args[0]
            self.assertEqual("ou_test", command[command.index("--user-id") + 1])
            with state_lock(kb):
                state = load_state_unlocked(kb, due_at)
            outbox_item = state["outbox"][completed["delivery"]["outbox_id"]]
            self.assertEqual("delivered", outbox_item["status"])
            self.assertEqual("reports/morning/2026-08-04.md", outbox_item["report_path"])

    def test_html_template_is_self_contained_and_has_required_slots(self):
        template_path = ROOT / "templates" / "report-template.html"
        template = template_path.read_text(encoding="utf-8")
        for placeholder in (
            "{{TITLE}}",
            "{{GENERATED_AT}}",
            "{{WINDOW_START}}",
            "{{WINDOW_END}}",
            "{{TIMEZONE}}",
            "{{COVERAGE_STATUS}}",
            "{{COVERAGE_NOTES}}",
            "{{SECTION_CARDS}}",
            "{{SOURCES}}",
            "{{MANUAL_NOTES}}",
        ):
            with self.subTest(placeholder=placeholder):
                self.assertIn(placeholder, template)
        lowered = template.lower()
        self.assertNotIn("<script src", lowered)
        self.assertNotIn("<link", lowered)
        self.assertNotIn("<img", lowered)
        self.assertNotIn("@import", lowered)
        self.assertNotIn("http://", lowered)
        self.assertNotIn("https://", lowered)

    def test_report_template_supporting_files_are_design_only(self):
        template_dir = ROOT / "templates"
        for name in (
            "report-template.html",
            "report-template.md",
        ):
            with self.subTest(name=name):
                path = template_dir / name
                self.assertTrue(path.is_file())
                lowered = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("<script src", lowered)
                self.assertNotIn("<link", lowered)
                self.assertNotIn("@import", lowered)
                self.assertNotIn("http://", lowered)
                self.assertNotIn("https://", lowered)

    def test_report_template_html_is_used_for_runtime_rendering(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            template = root / "report-template.html"
            template.write_text(
                "<!doctype html><title>{{title}}</title>"
                "<main data-template=\"runtime\">{{highlights}}</main>",
                encoding="utf-8",
            )
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(root, now)
            with mock.patch.object(
                dreaming_report_bundle,
                "HTML_TEMPLATE",
                template,
            ):
                rendered = render_report_bundle(kb, document=self.report_document())
            html_text = Path(rendered["html_path"]).read_text(encoding="utf-8")
            self.assertIn('data-template="runtime"', html_text)
            self.assertNotIn("{{title}}", html_text)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html_text)

    def test_report_template_missing_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            missing = root / "report-template.html"
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(root, now)
            with mock.patch.object(
                dreaming_report_bundle,
                "HTML_TEMPLATE",
                missing,
            ):
                with self.assertRaises(DreamingError) as caught:
                    render_report_bundle(kb, document=self.report_document())
            self.assertEqual("DREAMING_REPORT_TEMPLATE_MISSING", caught.exception.code)

    def test_report_template_rejects_external_resources(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            template = root / "report-template.html"
            template.write_text(
                '<!doctype html><link rel="stylesheet" href="https://example.test/a.css">',
                encoding="utf-8",
            )
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(root, now)
            with mock.patch.object(
                dreaming_report_bundle,
                "HTML_TEMPLATE",
                template,
            ):
                with self.assertRaises(DreamingError) as caught:
                    render_report_bundle(kb, document=self.report_document())
            self.assertEqual("DREAMING_REPORT_TEMPLATE_UNSAFE", caught.exception.code)

    def test_render_rejects_invalid_period_and_replaces_artifact_symlink(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(root, now)
            invalid = self.report_document()
            invalid["period"] = "../../outside"
            with self.assertRaises(DreamingError) as caught:
                render_report_bundle(kb, document=invalid)
            self.assertEqual(
                "DREAMING_REPORT_DOCUMENT_INVALID",
                caught.exception.code,
            )

            artifacts = (
                kb
                / "state"
                / "dreaming"
                / "reports"
                / "morning-2026-08-04"
                / "artifacts"
            )
            artifacts.mkdir(parents=True)
            external = root / "external.txt"
            external.write_text("unchanged", encoding="utf-8")
            os.symlink(external, artifacts / "summary.txt")
            rendered = render_report_bundle(kb, document=self.report_document())
            self.assertEqual("unchanged", external.read_text(encoding="utf-8"))
            self.assertFalse((artifacts / "summary.txt").is_symlink())
            self.assertTrue(Path(rendered["html_path"]).is_file())

    @mock.patch("dreaming_delivery_lark.subprocess.run")
    def test_lark_delivery_uses_bot_receipt_and_is_idempotent(self, run):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            render_report_bundle(kb, document=self.report_document())
            queued = enqueue_delivery(
                kb,
                kind="morning",
                period="2026-08-04",
                report_path="reports/morning/2026-08-04.md",
                commit="abc",
                channel="lark_bot",
                artifact="summary",
                recipient_id="ou_test",
                now=now,
            )
            run.return_value = mock.Mock(
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "message_id": "om_test",
                            "chat_id": "oc_test",
                        },
                    }
                ),
                stderr="",
            )
            delivered = deliver_lark_bot_summary(
                kb,
                outbox_id=queued["outbox_id"],
                binary="lark-cli",
                now=now,
            )
            self.assertEqual("om_test", delivered["delivery_id"])
            command = run.call_args.args[0]
            self.assertIn("--as", command)
            self.assertEqual("bot", command[command.index("--as") + 1])
            self.assertEqual("ou_test", command[command.index("--user-id") + 1])
            text = command[command.index("--text") + 1]
            self.assertIn("Byteworker Dreaming 晨报 · 2026-08-04", text)
            self.assertIn("\n- ", text)
            self.assertIn("完整报告：reports/morning/2026-08-04.md", text)
            repeated = deliver_lark_bot_summary(
                kb,
                outbox_id=queued["outbox_id"],
                binary="lark-cli",
                now=now,
            )
            self.assertEqual("om_test", repeated["delivery_id"])
            self.assertEqual(1, run.call_count)

    @mock.patch("dreaming_delivery_lark.subprocess.run")
    def test_pending_morning_delivery_skipped_at_noon_including_retry(self, send):
        with tempfile.TemporaryDirectory() as temporary:
            start = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), start)
            queued = enqueue_delivery(
                kb, kind="morning", period="2026-08-04", report_path="reports/morning/2026-08-04.md",
                commit="test", channel="lark_bot", artifact="summary", recipient_id="ou_test", now=start,
            )
            for delay in (2, 24):
                receipt = deliver_lark_bot_summary(kb, outbox_id=queued["outbox_id"], now=start + timedelta(hours=delay))
                self.assertEqual(("skipped", "morning_cutoff"), (receipt["status"], receipt["reason"]))
            send.assert_not_called()

    def test_lark_summary_message_is_structured_text(self):
        message = format_lark_summary_message(
            "示例条目甲。示例条目乙；示例条目丙。",
            {
                "kind": "morning",
                "period": "2026-08-07",
                "report_path": "reports/morning/2026-08-07.md",
            },
        )

        self.assertEqual(
            "\n".join(
                [
                    "Byteworker Dreaming 晨报 · 2026-08-07",
                    "",
                    "重点摘要",
                    "- 示例条目甲",
                    "- 示例条目乙",
                    "- 示例条目丙",
                    "",
                    "完整报告：reports/morning/2026-08-07.md",
                ]
            ),
            message,
        )

    @mock.patch("dreaming_delivery_lark.subprocess.run")
    def test_lark_failure_keeps_outbox_pending_and_local_artifacts(self, run):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            rendered = render_report_bundle(kb, document=self.report_document())
            queued = enqueue_delivery(
                kb,
                kind="morning",
                period="2026-08-04",
                report_path="reports/morning/2026-08-04.md",
                commit="abc",
                channel="lark_bot",
                artifact="summary",
                recipient_id="ou_test",
                now=now,
            )
            run.return_value = mock.Mock(
                returncode=1,
                stdout="",
                stderr=json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": 230001,
                            "message": "sensitive provider detail",
                        },
                    }
                ),
            )
            with self.assertRaises(DreamingError) as caught:
                deliver_lark_bot_summary(
                    kb,
                    outbox_id=queued["outbox_id"],
                    binary="lark-cli",
                    now=now,
                )
            self.assertEqual("DREAMING_DELIVERY_FAILED", caught.exception.code)
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            item = state["outbox"][queued["outbox_id"]]
            self.assertEqual("pending", item["status"])
            self.assertNotIn("sensitive provider detail", json.dumps(state))
            self.assertTrue(Path(rendered["html_path"]).is_file())
            self.assertTrue(
                (
                    kb
                    / "state"
                    / "dreaming"
                    / "reports"
                    / "morning-2026-08-04"
                    / "artifacts"
                    / "summary.txt"
                ).is_file()
            )

    def test_all_valid_routine_profiles_allow_owner_migration(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            source = {
                "source_key": "key",
                "source_type": "meego",
                "profile_revision": "sha256:test",
            }
            with mock.patch("dreaming_reports.routine_inventory", return_value=[source]):
                readiness = report_migration_readiness(kb)
            self.assertTrue(readiness["ready"])
            self.assertEqual(1, readiness["routine_source_count"])

            with mock.patch(
                "dreaming_reports.routine_inventory",
                side_effect=DreamingError(
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
