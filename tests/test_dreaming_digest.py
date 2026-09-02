import json
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
from dreaming_reports import report_dependency  # noqa: E402
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
