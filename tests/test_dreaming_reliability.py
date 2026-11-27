"""Failure injection for the deadline publisher and durable process handoff."""

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))

from digest_flow import start_flow
from dreaming_batch import commit_batch, create_collected_batch, recover_committed_cursors, write_stage_receipt
from dreaming_collection import prepare_im_batch
from dreaming_consolidation import consolidate_findings
from dreaming_daily import publish_due_daily
from dreaming_diagnostics import observed_run_due, run_tick
from dreaming_delivery_lark import deliver_lark_bot_summary
from dreaming_digest import link_routine_digest, prepare_routine_digest, record_routine_digest
from dreaming_grants import set_im_grant
from dreaming_report_completion import complete_report_run
from dreaming_scheduler import complete_run, configure, enable, heartbeat_run, run_due, set_report_management
from dreaming_state import DreamingError, load_state_unlocked, save_state_unlocked, secure_path
from source_profiles import profile_relative_path

try:
    from tests.test_source_profiles import feishu_chat_profile, feishu_doc_profile
except ModuleNotFoundError:
    from test_source_profiles import feishu_chat_profile, feishu_doc_profile

DUE = datetime(2026, 11, 27, 12, 30, tzinfo=timezone.utc)
PERIOD = DUE.date().isoformat()


class DreamingReliabilityTests(unittest.TestCase):
    def workspace(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        kb = Path(temporary.name)
        subprocess.run(["git", "init", "-q", str(kb)], check=True)
        (kb / "raw_data").mkdir()
        enable(kb, harness="fixture", timezone_name="Asia/Shanghai",
               acknowledge_machine_runtime=True, acknowledge_capability_tour=True,
               acknowledge_schedule=True, now=DUE - timedelta(hours=4))
        set_report_management(kb, enabled=True, acknowledge_owner_released=True, now=DUE - timedelta(hours=4))
        return kb

    def tick(self, kb, now):
        real_subprocess = subprocess.run

        def invoke(argv, **kwargs):
            if argv[0] != ROOT / "bin/byteworker":
                return real_subprocess(argv, **kwargs)
            if argv[1] == "preflight":
                return subprocess.CompletedProcess(argv, 0, b"{}")
            result = observed_run_due(
                kb, owner="fixture", lease_seconds=7200, followup="", tick_id=argv[-1],
            )
            return subprocess.CompletedProcess(argv, 0, json.dumps({"data": result}).encode())

        def publish(path, **kwargs):
            return publish_due_daily(path, now=now, **kwargs)

        with patch("dreaming_diagnostics.publish_due_daily", side_effect=publish), \
             patch("dreaming_diagnostics.subprocess.run", side_effect=invoke), \
             patch("dreaming_scheduler._now", return_value=now), \
             patch("operation_trace.os.environ", dict(os.environ)):
            return run_tick(kb, owner="fixture", lease_seconds=7200, root=ROOT)

    def document(self):
        return {
            "schema_version": "byteworker-report-document/v1", "kind": "daily", "period": PERIOD,
            "title": "正式日报样例", "generated_at": "2026-11-27 20:35",
            "window": {"start": "2026-11-27 00:00", "end": "2026-11-27 20:30", "timezone": "Asia/Shanghai"},
            "coverage": {"status": "covered", "notes": []},
            "message_summary": "合成日报摘要。",
            "sections": {"highlights": [], "changes": [], "risks": [], "confirmations": []},
            "sources": [], "manual_notes": "",
        }

    def test_tick_publishes_base_then_formal_daily_with_optional_delivery(self):
        for deliver in (False, True):
            with self.subTest(deliver=deliver):
                kb = self.workspace()
                configure(kb, lark_delivery_enabled=deliver, lark_recipient_id="ou_fixture", now=DUE)
                tick = self.tick(kb, DUE)
                self.assertEqual("daily", tick["lease"]["job"])
                base = tick["daily"]["published"][0]
                archive = kb / base["archive"]
                self.assertIn("已验收事项", archive.read_text())
                archive.write_text(archive.read_text().replace("- 暂无", "- 手动备注样例"))
                state = load_state_unlocked(kb, DUE)
                self.assertIn(PERIOD, state["jobs"]["daily"]["pending_periods"])
                self.assertEqual({}, state["outbox"])

                def send(argv, **kwargs):
                    persisted = load_state_unlocked(kb, DUE)
                    outbox_id = argv[argv.index("--idempotency-key") + 1]
                    self.assertNotIn(PERIOD, persisted["jobs"]["daily"]["pending_periods"])
                    self.assertIn(outbox_id, persisted["outbox"])
                    return subprocess.CompletedProcess(
                        argv, 0, '{"ok":true,"data":{"message_id":"om_fixture"}}', "",
                    )

                with patch("dreaming_delivery_lark.subprocess.run", side_effect=send) as provider:
                    result = complete_report_run(kb, token=tick["lease"]["token"], document=self.document(), now=DUE)
                    self.assertEqual(int(deliver), provider.call_count)
                repeated = self.tick(kb, DUE + timedelta(minutes=1))
                self.assertNotEqual("daily", (repeated.get("lease") or {}).get("job"))
                self.assertNotIn("daily", repeated)
                self.assertTrue(Path(result["rendered"]["html_path"]).is_file())
                self.assertIn("正式日报样例", archive.read_text())
                self.assertIn("手动备注样例", archive.read_text())
                self.assertEqual("delivered" if deliver else "skipped", result["delivery"]["status"])
                outbox = load_state_unlocked(kb, DUE)["outbox"]
                self.assertEqual(int(deliver), len(outbox))
                if deliver:
                    self.assertEqual("delivered", outbox[result["delivery"]["outbox_id"]]["status"])

    def test_deadline_runs_with_live_automatic_daily_lease_and_recovers_old_publisher_state(self):
        kb = self.workspace()
        original = run_due(kb, owner="fixture", now=DUE)["lease"]
        self.assertEqual("daily", original["job"])
        tick = self.tick(kb, DUE)
        self.assertEqual("busy", tick["status"])
        self.assertEqual(PERIOD, tick["daily"]["published"][0]["period"])
        state = load_state_unlocked(kb, DUE)
        self.assertEqual(original["token"], state["active_lease"]["token"])
        state["jobs"]["daily"]["pending_periods"].clear()
        state["active_lease"] = None
        save_state_unlocked(kb, state)
        recovered = run_due(kb, owner="fixture", now=DUE)["lease"]
        self.assertEqual((PERIOD, "daily"), (recovered["period"], recovered["job"]))

    def test_tick_respects_existing_user_archive(self):
        kb = self.workspace()
        report = kb / "reports" / "daily" / f"{PERIOD}.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# 用户撰写的日报样例\n")
        tick = self.tick(kb, DUE)
        self.assertNotEqual("daily", (tick.get("lease") or {}).get("job"))
        self.assertNotIn("daily", tick)
        self.assertEqual("# 用户撰写的日报样例\n", report.read_text())

    def test_tick_recovers_formal_debt_after_rendered_report_and_state_write_failure(self):
        kb = self.workspace()
        configure(kb, lark_delivery_enabled=True, lark_recipient_id="ou_fixture", now=DUE)
        tick = self.tick(kb, DUE)
        with patch("dreaming_scheduler._atomic_write", side_effect=OSError("state write failed")), \
             patch("dreaming_report_completion.deliver_lark_bot_summary") as provider:
            with self.assertRaises(OSError):
                complete_report_run(kb, token=tick["lease"]["token"], document=self.document(), now=DUE)
            provider.assert_not_called()
        artifacts = secure_path(kb, "reports", "daily-" + PERIOD, "artifacts")
        self.assertTrue((artifacts / "manifest.json").is_file())
        state = load_state_unlocked(kb, DUE)
        self.assertIn(PERIOD, state["jobs"]["daily"]["pending_periods"])
        self.assertEqual({}, state["outbox"])
        # Simulate the old deadline publisher consuming the formal queue entry.
        state["jobs"]["daily"]["pending_periods"].clear()
        save_state_unlocked(kb, state)
        recovered = self.tick(kb, DUE + timedelta(hours=2, minutes=1))
        self.assertEqual(PERIOD, recovered["lease"]["period"])
        self.assertEqual("daily", recovered["lease"]["job"])

    def test_report_completion_interruption_leaves_delivery_recoverable(self):
        kb = self.workspace()
        configure(kb, lark_delivery_enabled=True, lark_recipient_id="ou_fixture", now=DUE)
        tick = self.tick(kb, DUE)
        with patch("dreaming_scheduler.append_run_event", side_effect=OSError("after state persisted")), \
             patch("dreaming_report_completion.deliver_lark_bot_summary") as provider:
            with self.assertRaises(OSError):
                complete_report_run(kb, token=tick["lease"]["token"], document=self.document(), now=DUE)
            provider.assert_not_called()
        state = load_state_unlocked(kb, DUE)
        self.assertEqual(PERIOD, state["jobs"]["daily"]["last_success"]["period"])
        self.assertNotIn(PERIOD, state["jobs"]["daily"]["pending_periods"])
        outbox_id, pending = next(iter(state["outbox"].items()))
        self.assertEqual("pending", pending["status"])
        result = subprocess.CompletedProcess([], 0, '{"ok":true,"data":{"message_id":"om_fixture"}}', "")
        with patch("dreaming_delivery_lark.subprocess.run", return_value=result) as provider:
            for _ in range(2):
                sent = deliver_lark_bot_summary(kb, outbox_id=outbox_id, now=DUE)
                self.assertEqual("delivered", sent["status"])
            provider.assert_called_once()
            argv = provider.call_args.args[0]
            self.assertEqual(outbox_id, argv[argv.index("--idempotency-key") + 1])

    def finding(self, kb, grant, timestamp, summary, *, commit):
        when = timestamp.isoformat()
        batch = create_collected_batch(
            kb, source={"lane": "monitored", "source_type": "feishu_chat", "principal": "user:fixture"},
            window={"requested_start": when, "requested_end": (timestamp + timedelta(minutes=1)).isoformat()},
            coverage={"status": "complete", "gaps": []}, messages=[], grant_revision=grant["revision"],
            now=timestamp,
        )["batch_id"]
        consolidate_findings(kb, batch_id=batch, bundle={"findings": [{
            "finding_id": "fixture-" + summary, "summary": summary, "occurred_at": when,
            "evidence_refs": ["message:om_fixture"],
        }]}, expected_grant_revision=grant["revision"], now=timestamp)
        write_stage_receipt(kb, batch_id=batch, stage="consolidated", receipt={}, now=timestamp)
        if commit:
            commit_batch(kb, batch_id=batch, cursor_key="im:monitored",
                         through=(timestamp + timedelta(minutes=1)).isoformat(), now=timestamp)
        return batch

    def test_independent_daily_filters_uncommitted_history_and_supplements(self):
        kb = self.workspace()
        grant = set_im_grant(kb, mode="monitored", persist_finding=True,
                             acknowledge_all_visible=False, now=DUE - timedelta(hours=1))
        start = DUE - timedelta(minutes=20)
        lease = run_due(kb, owner="process-worker", now=start)["lease"]
        self.assertEqual("process", lease["job"])
        self.assertEqual("idle", publish_due_daily(kb, now=DUE - timedelta(seconds=1))["status"])

        self.finding(kb, grant, start, "已验收事项样例", commit=True)
        late = self.finding(kb, grant, start + timedelta(minutes=3), "迟到事项样例", commit=False)
        with patch("dreaming_report_bundle._render_html", side_effect=OSError("render unavailable")):
            first = publish_due_daily(kb, now=DUE, supplement=True)["published"][0]
        self.assertEqual(1, first["revision"])
        state = load_state_unlocked(kb, DUE)
        self.assertEqual(lease["token"], state["active_lease"]["token"])
        report = kb / first["archive"]
        self.assertIn("已验收事项样例", report.read_text())
        self.assertNotIn("迟到事项样例", report.read_text())
        self.assertEqual([], publish_due_daily(kb, now=DUE)["published"])

        report.write_text(report.read_text().replace("- 暂无", "- 我的备注"))
        commit_batch(kb, batch_id=late, cursor_key="im:monitored",
                     through=(start + timedelta(minutes=4)).isoformat(), now=DUE + timedelta(minutes=4))
        second = publish_due_daily(kb, now=DUE + timedelta(minutes=5), supplement=True)["published"][0]
        self.assertEqual(2, second["revision"])
        self.assertEqual(first["published_at"], second["published_at"])
        self.assertIn("迟到事项样例", report.read_text())
        self.assertIn("我的备注", report.read_text())

    def test_daily_reads_committed_event_snapshot_without_worktree_edits(self):
        kb = self.workspace()
        node = kb / "knowledge" / "events" / "event-fixture.md"
        node.parent.mkdir(parents=True)
        node.write_text("---\nid: event-fixture\nprimary_source: raw-fixture\n---\n"
                        "\n> **TL;DR:** 已归档事件样例\n")
        raw = kb / "raw_data" / "fixture.md"
        raw.write_text("---\nraw_id: raw-fixture\ndigest_status: digested\n"
                       "event_time: 2026-11-27T11:00:00+00:00\n"
                       "source_url: https://example.test/fixture\n---\n\n原始记录。\n")
        subprocess.run(["git", "-C", str(kb), "add", "knowledge", "raw_data"], check=True)
        stamp = (DUE - timedelta(minutes=20)).isoformat()
        subprocess.run(["git", "-C", str(kb), "-c", "user.name=Fixture", "-c",
                        "user.email=fixture@example.test", "commit", "-qm", "Synthetic event"],
                       check=True, env={**os.environ, "GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
        node.write_text(node.read_text().replace("已归档事件样例", "尚未提交的改动"))
        receipt = publish_due_daily(kb, now=DUE)["published"][0]
        report = (kb / receipt["archive"]).read_text()
        self.assertIn("已归档事件样例", report)
        self.assertIn("https://example.test/fixture", report)
        self.assertNotIn("尚未提交的改动", report)

    def test_archive_switch_failure_replays_the_staged_version(self):
        kb = self.workspace()
        with patch("dreaming_daily._write_archive", side_effect=OSError("injected disk failure")):
            with self.assertRaises(OSError):
                publish_due_daily(kb, now=DUE)
        receipt_path = secure_path(kb, "reports", "daily-2026-11-27", "deadline", "publication.json")
        staged = json.loads(receipt_path.read_text())
        self.assertEqual("staged", staged["status"])
        self.assertFalse((kb / staged["archive"]).is_file())

        final = publish_due_daily(kb, now=DUE + timedelta(minutes=4))["published"][0]
        self.assertEqual(staged["sha256"], final["sha256"])
        self.assertEqual("published", final["status"])
        self.assertEqual(final["sha256"], hashlib.sha256((kb / final["archive"]).read_bytes()).hexdigest())
        later = load_state_unlocked(kb, DUE + timedelta(minutes=4))
        incident = later["jobs"]["daily"]["publication_incidents"][final["period"]]
        self.assertEqual("critical", incident["severity"])
        self.assertEqual(final["published_at"], incident["recovered_at"])

    def test_progress_renews_lease_and_takeover_recovers_source_receipt_and_link(self):
        kb = self.workspace()
        for profile in (feishu_doc_profile(), feishu_chat_profile()):
            path = kb / profile_relative_path(profile)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(profile))
        when = DUE - timedelta(minutes=10)
        first = run_due(kb, owner="one", lease_seconds=60, now=when)["lease"]
        token = first["token"]
        batch = prepare_routine_digest(kb, token=token, now=when)
        source = next(item for item in batch["sources"] if item["source_type"] == "feishu_doc")
        chat = next(item for item in batch["sources"] if item["source_type"] == "feishu_chat")
        flow = start_flow(kb, source_type=chat["source_type"], source_ref=chat["source_uid"])
        with patch("dreaming_digest._now", return_value=when), patch("dreaming_scheduler._now", return_value=when):
            link_routine_digest(kb, token=token, source_key=chat["source_key"], digest_run_id=flow["run_id"])
        digest_key = f"{source['source_type']}:{source['source_uid']}:-:fixture"
        (kb / "raw_data" / "fixture.md").write_text(
            f"---\nraw_id: raw-fixture\nsource_type: feishu_doc\nsource_uid: {source['source_uid']}\n"
            f"digest_status: digested\ndigest_key: {digest_key}\n---\n\nFixture.\n"
        )
        new_expiry = heartbeat_run(kb, token=token, stage="analysis", now=when + timedelta(seconds=59))
        self.assertEqual(when + timedelta(seconds=119), datetime.fromisoformat(new_expiry["expires_at"]))
        with patch("dreaming_digest.save_state_unlocked", side_effect=OSError("after receipt before checkpoint")):
            with self.assertRaises(OSError):
                record_routine_digest(kb, token=token, now=when + timedelta(seconds=61), result={
                    **source, "status": "noop", "receipt": {
                        "status": "noop", "digest_key": digest_key, "raw_id": "raw-fixture",
                    },
                })
        expired = when + timedelta(seconds=120)
        with self.assertRaises(DreamingError) as stale:
            complete_run(kb, token=token, run_status="success", now=expired)
        self.assertEqual("DREAMING_LEASE_MISMATCH", stale.exception.code)
        second = run_due(kb, owner="two", now=expired)["lease"]
        self.assertEqual(first["work_id"], second["work_id"])
        self.assertNotEqual(first["token"], second["token"])
        resumed = prepare_routine_digest(kb, token=second["token"], now=expired)
        self.assertEqual(batch["target_through"], resumed["target_through"])
        items = {item["source_type"]: item for item in resumed["sources"]}
        self.assertEqual("noop", items["feishu_doc"]["status"])
        self.assertEqual(flow["run_id"], items["feishu_chat"]["work"]["digest_run_id"])
        state = load_state_unlocked(kb, expired)
        self.assertIn(source["source_key"], state["source_checkpoints"])

    def test_im_capture_and_commit_marker_survive_worker_restart(self):
        kb = self.workspace()
        set_im_grant(kb, mode="all_visible", persist_finding=True,
                     acknowledge_all_visible=True, now=DUE - timedelta(hours=1))
        when = DUE - timedelta(minutes=10)
        first = run_due(kb, owner="one", lease_seconds=60, now=when)["lease"]
        collector = Mock()
        collector.principal.return_value = "user:fixture"
        collector.collect_discovery.return_value = {"coverage": {"status": "best_effort", "gaps": []}, "messages": []}
        with patch("dreaming_batch.save_state_unlocked", side_effect=OSError("after capture before index")):
            with self.assertRaises(OSError):
                prepare_im_batch(kb, collector=collector, process_token=first["token"], now=when)
        intent = next(secure_path(kb, "im-work", first["work_id"]).glob("*.json"))
        batch_id = intent.stem
        later = when + timedelta(minutes=2)
        second = run_due(kb, owner="two", now=later)["lease"]
        batch = prepare_im_batch(kb, collector=collector, process_token=second["token"], now=later)
        self.assertEqual(batch_id, batch["batch_id"])
        self.assertEqual(1, collector.collect_discovery.call_count)
        write_stage_receipt(kb, batch_id=batch["batch_id"], stage="consolidated", receipt={}, now=later)
        with self.assertRaises(DreamingError):
            commit_batch(kb, batch_id=batch["batch_id"], process_token=first["token"],
                         cursor_key="im:discovery", through=batch["window"]["end"], now=later)
        with patch("dreaming_batch.save_state_unlocked", side_effect=OSError("after marker before state")):
            with self.assertRaises(OSError):
                commit_batch(kb, batch_id=batch["batch_id"], process_token=second["token"],
                             cursor_key="im:discovery", through=batch["window"]["end"], now=later)
        self.assertNotIn(batch["batch_id"], load_state_unlocked(kb, later)["receipt_index"])
        self.assertEqual(1, recover_committed_cursors(kb, now=later)["repaired"])
        self.assertEqual(0, recover_committed_cursors(kb, now=later)["repaired"])
        self.assertIn(batch["batch_id"], load_state_unlocked(kb, later)["receipt_index"])
