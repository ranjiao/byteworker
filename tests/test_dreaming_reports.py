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
)
from dreaming_report_bundle import render_report_bundle  # noqa: E402
from dreaming_report_completion import complete_report_run  # noqa: E402
from dreaming_delivery_lark import deliver_lark_bot_summary  # noqa: E402
from dreaming_scheduler import complete_run, configure, enable, run_due  # noqa: E402
from dreaming_state import (  # noqa: E402
    DreamingError,
    load_state_unlocked,
    save_state_unlocked,
    state_lock,
)
from source_profile_contract import SourceProfileError  # noqa: E402
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
        summary = (
            "今天需要重点关注三件事。第一，内容安全评测口径仍有两处差异，建议上午完成确认，"
            "避免影响下午的灰度判断。第二，模型迁移计划进入首批验证阶段，但回滚负责人和异常"
            "阈值尚未形成书面结论，启动前需要补齐。第三，夜间出现一次稳定性告警，目前只能"
            "确认现象，尚不能判断根因。最高风险是未明确的回滚条件可能扩大故障影响。待确认项"
            "包括是否按当前范围启动灰度，以及线上和离线指标采用哪套口径。详细事实、Todo、"
            "覆盖说明和原始引用请查看 HTML 晨报；本摘要只用于快速提醒，不替代完整报告。"
            "若今天只能处理一项，优先完成回滚条件和负责人确认，并把结论同步给相关团队。"
            "其他夜间变化暂未达到需要立即处理的门槛，可在完成上述事项后再查看。由于当前使用"
            "尽力扫描，未出现的信息不代表没有变化，关键决策仍应回到原始来源复核。"
        )
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
                        "title": "<script>alert(1)</script>评测口径待确认",
                        "detail": "下午灰度前需要形成结论。",
                        "severity": "attention",
                        "source_refs": ["S1"],
                    }
                ],
                "changes": [],
                "risks": [],
                "confirmations": [],
                "todos": [],
            },
            "sources": [
                {
                    "id": "S1",
                    "title": "评测讨论",
                    "type": "飞书群聊",
                    "locator": "chat_id=oc_test",
                    "observed_at": "2026-08-04 09:00",
                    "confidence": "高",
                }
            ],
            "manual_notes": "",
        }

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

    def test_render_bundle_is_host_neutral_and_escapes_html(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            rendered = render_report_bundle(kb, document=self.report_document())
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

            completed = complete_report_run(
                kb,
                token=leased["lease"]["token"],
                document=self.report_document(),
                item_count=1,
                finding_count=1,
                gap_count=0,
            )

            self.assertEqual("completed", completed["status"])
            self.assertEqual("skipped", completed["delivery"]["status"])
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
            repeated = deliver_lark_bot_summary(
                kb,
                outbox_id=queued["outbox_id"],
                binary="lark-cli",
                now=now,
            )
            self.assertEqual("om_test", repeated["delivery_id"])
            self.assertEqual(1, run.call_count)

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
