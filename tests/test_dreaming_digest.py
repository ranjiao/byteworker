import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_digest import (  # noqa: E402
    RESULT_SCHEMA,
    complete_routine_digest,
    prepare_routine_digest,
    record_routine_digest_result,
    routine_inventory,
)
from digest_flow import flow_status, prepare_flow, start_flow  # noqa: E402
from dreaming_reports import report_dependency  # noqa: E402
from dreaming_run_log import show_run  # noqa: E402
from dreaming_run_result import RESULT_SCHEMA as RUN_RESULT_SCHEMA  # noqa: E402
from dreaming_scheduler import complete_run, enable, run_due  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402
from source_profiles import profile_relative_path  # noqa: E402
try:
    from tests.test_source_profiles import (  # noqa: E402
        feishu_base_profile,
        feishu_chat_profile,
        feishu_doc_profile,
        feishu_wiki_profile,
        profile as aeolus_profile,
    )
except ModuleNotFoundError:
    from test_source_profiles import (  # type: ignore[no-redef]  # noqa: E402
        feishu_base_profile,
        feishu_chat_profile,
        feishu_doc_profile,
        feishu_wiki_profile,
        profile as aeolus_profile,
    )


class DreamingDigestTests(unittest.TestCase):
    def make_kb(self, root: Path, now: datetime) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        (kb / "sources").mkdir()
        (kb / "raw_data").mkdir()
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

    def profile(self) -> dict:
        return {
            "schema_version": "byteworker-source-profile/v2",
            "source_type": "meego",
            "source_uid": "meego:safety:view-42",
            "source_url": "https://project.feishu.cn/safety/view/view-42",
            "title": "安全需求视图",
            "selector": {
                "project_key": "safety",
                "view_id": "view-42",
            },
            "capture_policy": {
                "fields": ["updated_at", "name", "status"],
                "max_items": 500,
            },
            "routine": {"enabled": True, "cadence": "daily"},
        }

    def save_profile(self, kb: Path, profile: dict) -> None:
        path = kb / profile_relative_path(profile)
        path.write_text(
            json.dumps(profile, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def write_noop_raw(self, kb: Path, digest_key: str) -> None:
        (kb / "raw_data" / "meego.md").write_text(
            "\n".join(
                [
                    "---",
                    "raw_id: raw-meego",
                    "ingested: 2026-08-04T10:00:00+08:00",
                    "source_type: meego",
                    "source_uid: meego:safety:view-42",
                    f"digest_key: {digest_key}",
                    "digest_status: digested",
                    "digest_targets:",
                    "  - reading-meego",
                    "---",
                    "",
                    "snapshot",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def test_process_requires_all_routine_digest_receipts(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            profile = self.profile()
            self.save_profile(kb, profile)
            leased = run_due(kb, owner="host", now=enabled_at + timedelta(hours=2))

            prepared = prepare_routine_digest(
                kb,
                token=leased["lease"]["token"],
                now=enabled_at + timedelta(hours=2),
            )

            self.assertEqual(1, prepared["source_count"])
            self.assertEqual("meego", prepared["sources"][0]["source_type"])
            with self.assertRaises(DreamingError) as caught:
                complete_run(
                    kb,
                    token=leased["lease"]["token"],
                    run_status="success",
                    now=enabled_at + timedelta(hours=2, minutes=1),
                )
            self.assertEqual("DREAMING_DIGEST_INCOMPLETE", caught.exception.code)

    def test_observed_status_is_rejected_for_ordinary_digest(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            self.save_profile(kb, self.profile())
            leased = run_due(kb, owner="host", now=enabled_at + timedelta(hours=2))
            prepared = prepare_routine_digest(
                kb,
                token=leased["lease"]["token"],
                now=enabled_at + timedelta(hours=2),
            )
            source = prepared["sources"][0]
            digest_key = "meego:meego:safety:view-42:-:abc123"
            self.write_noop_raw(kb, digest_key)
            result_path = Path(temporary) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": RESULT_SCHEMA,
                        "source_key": source["source_key"],
                        "source_type": source["source_type"],
                        "source_uid": source["source_uid"],
                        "profile_revision": source["profile_revision"],
                        "status": "observed",
                        "receipt": {
                            "status": "observed",
                            "raw_id": "raw-meego",
                            "digest_key": digest_key,
                        },
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaises(DreamingError) as caught:
                record_routine_digest_result(
                    kb,
                    token=leased["lease"]["token"],
                    input_path=result_path,
                    now=enabled_at + timedelta(hours=2, minutes=1),
                )

            self.assertEqual("DREAMING_DIGEST_RESULT_INVALID", caught.exception.code)

    def test_noop_digest_advances_source_checkpoint_and_unblocks_report(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            profile = self.profile()
            self.save_profile(kb, profile)
            leased = run_due(kb, owner="host", now=enabled_at + timedelta(hours=2))
            prepared = prepare_routine_digest(
                kb,
                token=leased["lease"]["token"],
                now=enabled_at + timedelta(hours=2),
            )
            source = prepared["sources"][0]
            digest_key = "meego:meego:safety:view-42:-:abc123"
            self.write_noop_raw(kb, digest_key)
            result_path = Path(temporary) / "result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": RESULT_SCHEMA,
                        "source_key": source["source_key"],
                        "source_type": source["source_type"],
                        "source_uid": source["source_uid"],
                        "profile_revision": source["profile_revision"],
                        "status": "noop",
                        "receipt": {
                            "status": "noop",
                            "raw_id": "raw-meego",
                            "digest_key": digest_key,
                        },
                    }
                ),
                encoding="utf-8",
            )
            recorded = record_routine_digest_result(
                kb,
                token=leased["lease"]["token"],
                input_path=result_path,
                now=enabled_at + timedelta(hours=2, minutes=1),
            )
            self.assertEqual("noop", recorded["status"])
            completed = complete_routine_digest(
                kb,
                token=leased["lease"]["token"],
                now=enabled_at + timedelta(hours=2, minutes=2),
            )
            self.assertEqual(1, completed["noop_count"])
            complete_run(
                kb,
                token=leased["lease"]["token"],
                run_status="success",
                now=enabled_at + timedelta(hours=2, minutes=3),
            )

            dependency = report_dependency(
                kb,
                kind="morning",
                period="2026-08-04",
            )
            self.assertEqual("covered", dependency["status"])

    def test_verified_empty_chat_window_completes_without_a_transaction(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            acquired = enabled_at + timedelta(hours=4)
            kb = self.make_kb(root, enabled_at)
            chat_profile = feishu_chat_profile()
            self.save_profile(kb, chat_profile)
            lease = run_due(kb, owner="host", now=acquired)["lease"]
            token = lease["token"]
            batch = prepare_routine_digest(kb, token=token, now=acquired)
            source = batch["sources"][0]
            flow = start_flow(kb, source_type="feishu_chat", source_ref=source["source_uid"])
            work_dir = Path(flow["work_dir"])
            transcript = work_dir / "transcript.txt"
            transcript.touch()
            end = acquired + timedelta(seconds=30)
            window = f"{enabled_at.isoformat()} .. {end.isoformat()}"
            request = work_dir / "bundle-request.json"
            request.write_text(json.dumps({
                "source_uid": source["source_uid"],
                "title": chat_profile["title"],
                "source_window": window,
                "transcript": {"path": str(transcript)},
                "locator_artifact": {
                    "schema_version": "byteworker-source-locators/v1",
                    "source_type": "feishu_chat",
                    "source_chat_id": source["source_uid"],
                    "source_window": window,
                    "anchors": [],
                },
                "provider_metadata": {
                    "capture_mode": "since-last",
                    "message_count": 0,
                    "page_count": 1,
                    "source_profile": {"revision": source["profile_revision"]},
                },
            }), encoding="utf-8")
            prepared = prepare_flow(kb, run_id=flow["run_id"], bundle_request=request)
            self.assertEqual(("noop", "none"), (prepared["phase"], prepared["next_action"]))
            receipt = prepared["receipt"]
            self.assertEqual("empty_window", receipt["status"])
            recovered = flow_status(kb, run_id=flow["run_id"])
            self.assertEqual(receipt, recovered["receipt"])
            self.assertEqual(["classify", "bundle"], [stage["stage"] for stage in recovered["run"]["stages"]])

            result_path = root / "result.json"
            result_path.write_text(json.dumps({
                **source, "schema_version": RESULT_SCHEMA,
                "status": "empty_window", "receipt": receipt,
            }), encoding="utf-8")
            with self.assertRaisesRegex(DreamingError, "不是本轮抓取"):
                record_routine_digest_result(kb, token=token, input_path=result_path, now=acquired)
            transcript.write_text("nonempty", encoding="utf-8")
            recorded_at = end + timedelta(seconds=1)
            with self.assertRaisesRegex(DreamingError, "配置版本不符"):
                record_routine_digest_result(kb, token=token, input_path=result_path, now=recorded_at)
            transcript.write_text("", encoding="utf-8")
            recorded = record_routine_digest_result(kb, token=token, input_path=result_path, now=recorded_at)
            self.assertEqual("empty_window", recorded["status"])
            completed = complete_routine_digest(kb, token=token, now=recorded_at)
            self.assertEqual(1, completed["empty_window_count"])
            complete_run(kb, token=token, run_status="success", now=recorded_at)
            self.assertEqual("covered", report_dependency(kb, kind="morning", period="2026-08-04")["status"])
            self.assertEqual([], list((kb / "raw_data").iterdir()))

    def test_failed_run_keeps_private_summary_and_counts_only_recorded_writes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            acquired = enabled_at + timedelta(hours=4)
            finished = acquired + timedelta(minutes=3)
            kb = self.make_kb(root, enabled_at)
            self.save_profile(kb, self.profile())
            self.save_profile(kb, feishu_chat_profile())

            def git(*args):
                return subprocess.check_output(
                    ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
                    cwd=kb, text=True,
                ).strip()

            git("init", "-q")
            (kb / "knowledge").mkdir()
            existing = kb / "knowledge" / "reading-existing.md"
            existing.write_text("Before\n")
            git("add", "knowledge")
            git("commit", "-qm", "initial")
            lease = run_due(kb, owner="host", now=acquired)["lease"]
            batch = prepare_routine_digest(kb, token=lease["token"], now=acquired)
            source = next(item for item in batch["sources"] if item["source_type"] == "meego")

            digest_key = "meego:meego:safety:view-42:-:abc123"
            self.write_noop_raw(kb, digest_key)
            existing.write_text("After\n")
            (kb / "knowledge" / "reading-new.md").write_text("New\n")
            git("add", "knowledge", "raw_data")
            git("commit", "-qm", "digest")
            result_path = root / "source-result.json"
            result_path.write_text(json.dumps({
                **source, "schema_version": RESULT_SCHEMA, "status": "committed",
                "receipt": {
                    "status": "committed", "raw_path": "raw_data/meego.md",
                    "digest_key": digest_key, "commit": git("rev-parse", "HEAD"),
                },
            }))
            record_routine_digest_result(kb, token=lease["token"], input_path=result_path, now=finished)

            summary = "仅完成一个来源，另一个来源未登记完成。"
            completed = complete_run(
                kb, token=lease["token"], run_status="failed", error_code="SOURCE_AUTH_REQUIRED", now=finished,
                result_document={
                    "schema_version": RUN_RESULT_SCHEMA, "job": "process", "period": lease["period"],
                    "summary": summary, "source_notes": [{"source_key": source["source_key"], "summary": "一增一改"}],
                },
            )
            logged = show_run(kb, run_id=lease["run_id"])
            self.assertEqual(completed["summary"], logged["summary"])
            self.assertEqual(summary, logged["summary"]["summary"])
            accounting = logged["summary"]["accounting"]
            self.assertEqual(180_000, accounting["metrics"]["duration_ms"])
            self.assertEqual(1, accounting["unaccounted_sources"])
            self.assertEqual(
                {"digest_commits": 1, "files_changed": 3, "nodes_created": 1, "nodes_updated": 1, "nodes_deleted": 0},
                accounting["kb_operations"],
            )
            self.assertEqual({"committed", "pending"}, {row["status"] for row in accounting["routine_sources"]})
            self.assertEqual(completed["result_path"], logged["events"][-1]["result_path"])
            self.assertNotIn(summary, json.dumps(logged["events"], ensure_ascii=False))
            self.assertNotIn("summary", show_run(kb, run_id=lease["run_id"], include_summary=False))

    def test_inventory_change_prevents_checkpoint_advance(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            enabled_at = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), enabled_at)
            profile = self.profile()
            self.save_profile(kb, profile)
            leased = run_due(kb, owner="host", now=enabled_at + timedelta(hours=2))
            prepare_routine_digest(
                kb,
                token=leased["lease"]["token"],
                now=enabled_at + timedelta(hours=2),
            )
            profile["capture_policy"]["max_items"] = 600
            self.save_profile(kb, profile)

            with self.assertRaises(DreamingError) as caught:
                complete_routine_digest(
                    kb,
                    token=leased["lease"]["token"],
                    now=enabled_at + timedelta(hours=2, minutes=1),
                )
            self.assertIn(
                caught.exception.code,
                {"DREAMING_DIGEST_INCOMPLETE", "DREAMING_DIGEST_INVENTORY_CHANGED"},
            )

    def test_inventory_covers_all_profile_source_types(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            profiles = [
                self.profile(),
                aeolus_profile(),
                feishu_doc_profile(),
                feishu_base_profile(),
                feishu_chat_profile(),
                feishu_wiki_profile(),
            ]
            for value in profiles:
                self.save_profile(kb, value)

            inventory = routine_inventory(kb)

            self.assertEqual(
                {
                    "aeolus",
                    "feishu_base",
                    "feishu_chat",
                    "feishu_doc",
                    "feishu_wiki",
                    "meego",
                },
                {item["source_type"] for item in inventory},
            )
            workflows = {item["source_type"]: item["workflow"] for item in inventory}
            self.assertEqual("wiki_scan_then_digest", workflows["feishu_wiki"])
            self.assertTrue(
                all(
                    workflow == "ordinary_digest"
                    for source_type, workflow in workflows.items()
                    if source_type != "feishu_wiki"
                )
            )

    def test_legacy_routine_raw_is_included_and_profile_supersedes_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            legacy = kb / "raw_data" / "legacy.md"
            legacy.write_text(
                "\n".join(
                    [
                        "---",
                        "raw_id: raw-legacy",
                        "source_type: feishu_doc",
                        "source_uid: docx123",
                        "source_url: https://example.test/docx/docx123",
                        "routine: weekly",
                        "digest_status: digested",
                        "---",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            inventory = routine_inventory(kb)
            self.assertEqual("legacy_raw", inventory[0]["origin"])

            self.save_profile(kb, feishu_doc_profile())
            inventory = routine_inventory(kb)
            self.assertEqual(1, len(inventory))
            self.assertEqual("profile", inventory[0]["origin"])

    def test_legacy_routine_recovers_uid_from_newer_raw_with_same_url(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            (kb / "raw_data" / "legacy.md").write_text(
                "---\n"
                "source_type: feishu_doc\n"
                "source_uid:\n"
                "source_url: https://example.test/wiki/wiki123\n"
                "routine: weekly\n"
                "---\n"
                "body\n",
                encoding="utf-8",
            )
            (kb / "raw_data" / "middle.md").write_text(
                "---\n"
                "source_type: feishu_doc\n"
                "source_uid: stale-wiki-token\n"
                "source_url: https://example.test/wiki/wiki123\n"
                "ingested: 2026-08-03T00:00:00Z\n"
                "---\n"
                "body\n",
                encoding="utf-8",
            )
            (kb / "raw_data" / "newest.md").write_text(
                "---\n"
                "source_type: feishu_doc\n"
                "source_uid: docx123\n"
                "source_url: https://example.test/wiki/wiki123\n"
                "ingested: 2026-08-04T00:00:00Z\n"
                "---\n"
                "body\n",
                encoding="utf-8",
            )

            inventory = routine_inventory(kb)

            self.assertEqual(1, len(inventory))
            self.assertEqual("docx123", inventory[0]["source_uid"])
            self.assertEqual("raw_data/legacy.md", inventory[0]["raw_path"])

    def write_legacy_raw(self, kb, name, **fields):
        path = kb / "raw_data" / name
        path.write_text(
            "\n".join(["---", *(f"{key}: {value}" for key, value in fields.items()), "---", "body", ""]),
            encoding="utf-8",
        )
        return path

    def test_legacy_routine_preserves_explicit_uid(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
            self.write_legacy_raw(
                kb, "a-routine.md", source_type="feishu_doc", source_uid="explicit-uid",
                source_url="https://example.test/wiki/shared", routine="weekly",
                ingested="2026-08-03T00:00:00Z",
            )
            self.write_legacy_raw(
                kb, "z-other.md", source_type="feishu_doc", source_uid="different-uid",
                source_url="https://example.test/wiki/shared", ingested="2026-08-04T00:00:00Z",
            )

            inventory = routine_inventory(kb)

            self.assertEqual(["explicit-uid"], [item["source_uid"] for item in inventory])

    def test_legacy_uid_recovery_uses_ingested_time_not_filename(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
            self.write_legacy_raw(
                kb, "legacy.md", source_type="feishu_doc",
                source_url="https://example.test/wiki/shared", routine="weekly",
            )
            self.write_legacy_raw(
                kb, "z-old.md", source_type="feishu_doc", source_uid="old-uid",
                source_url="https://example.test/wiki/shared", ingested="2026-08-05T00:30:00+08:00",
            )
            self.write_legacy_raw(
                kb, "a-new.md", source_type="feishu_doc", source_uid="new-uid",
                source_url="https://example.test/wiki/shared", ingested="2026-08-04T17:00:00Z",
            )
            before = {path: path.read_bytes() for path in (kb / "raw_data").glob("*.md")}

            inventory = routine_inventory(kb)

            self.assertEqual(["new-uid"], [item["source_uid"] for item in inventory])
            self.assertEqual("raw_data/legacy.md", inventory[0]["raw_path"])
            self.assertEqual(before, {path: path.read_bytes() for path in before})

    def test_legacy_uid_recovery_fails_closed_when_chronology_is_ambiguous(self):
        for uncertain_time in (
            None, "not-a-time", "2026-08-04", "2026-08-04T00:00:00",
            "2026-08-04T08:00:00+08:00",
        ):
            with self.subTest(ingested=uncertain_time), tempfile.TemporaryDirectory() as temporary:
                kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
                self.write_legacy_raw(
                    kb, "legacy.md", source_type="feishu_doc",
                    source_url="https://example.test/wiki/shared", routine="weekly",
                )
                self.write_legacy_raw(
                    kb, "a.md", source_type="feishu_doc", source_uid="uid-a",
                    source_url="https://example.test/wiki/shared", ingested="2026-08-04T00:00:00Z",
                )
                fields = {"ingested": uncertain_time} if uncertain_time is not None else {}
                self.write_legacy_raw(
                    kb, "z.md", source_type="feishu_doc", source_uid="uid-b",
                    source_url="https://example.test/wiki/shared", **fields,
                )

                with self.assertRaises(DreamingError) as caught:
                    routine_inventory(kb)

                self.assertEqual("DREAMING_ROUTINE_SOURCE_INVALID", caught.exception.code)

    def test_legacy_uid_recovery_accepts_consistent_uid_without_timestamps(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
            self.write_legacy_raw(
                kb, "legacy.md", source_type="feishu_doc",
                source_url="https://example.test/wiki/shared", routine="weekly",
            )
            for name in ("a.md", "z.md"):
                self.write_legacy_raw(
                    kb, name, source_type="feishu_doc", source_uid="consistent-uid",
                    source_url="https://example.test/wiki/shared",
                )

            inventory = routine_inventory(kb)

            self.assertEqual(["consistent-uid"], [item["source_uid"] for item in inventory])

    def test_legacy_uid_recovery_requires_same_source_identity(self):
        for other_type, other_url in (
            ("feishu_chat", "https://example.test/wiki/shared"),
            ("feishu_doc", "https://example.test/wiki/other"),
        ):
            with self.subTest(source_type=other_type, source_url=other_url), tempfile.TemporaryDirectory() as temporary:
                kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
                self.write_legacy_raw(
                    kb, "legacy.md", source_type="feishu_doc",
                    source_url="https://example.test/wiki/shared", routine="weekly",
                )
                self.write_legacy_raw(
                    kb, "other.md", source_type=other_type, source_uid="unrelated-uid",
                    source_url=other_url,
                )

                with self.assertRaises(DreamingError) as caught:
                    routine_inventory(kb)

                self.assertEqual("DREAMING_ROUTINE_SOURCE_INVALID", caught.exception.code)

    def test_legacy_chat_recovery_respects_enabled_and_disabled_profiles(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), tempfile.TemporaryDirectory() as temporary:
                kb = self.make_kb(Path(temporary), datetime(2026, 8, 3, tzinfo=timezone.utc))
                profile = feishu_chat_profile()
                profile["routine"]["enabled"] = enabled
                if not enabled:
                    profile["routine"]["cadence"] = ""
                self.save_profile(kb, profile)
                self.write_legacy_raw(
                    kb, "legacy.md", source_type="feishu_chat",
                    source_chat_id=profile["source_uid"], routine="weekly",
                )
                self.write_legacy_raw(
                    kb, "newer.md", source_type="feishu_chat", source_uid=profile["source_uid"],
                    source_chat_id=profile["source_uid"], routine="weekly",
                )

                inventory = routine_inventory(kb)

                self.assertEqual(1 if enabled else 0, len(inventory))
                if enabled:
                    self.assertEqual("profile", inventory[0]["origin"])
                    self.assertEqual(profile["routine"]["cadence"], inventory[0]["cadence"])

    def test_legacy_routine_without_identity_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            now = datetime(2026, 8, 3, 23, tzinfo=timezone.utc)
            kb = self.make_kb(Path(temporary), now)
            (kb / "raw_data" / "legacy.md").write_text(
                "---\nsource_type: web\nroutine: weekly\n---\nbody\n",
                encoding="utf-8",
            )

            with self.assertRaises(DreamingError) as caught:
                routine_inventory(kb)

            self.assertEqual(
                "DREAMING_ROUTINE_SOURCE_INVALID",
                caught.exception.code,
            )


if __name__ == "__main__":
    unittest.main()
