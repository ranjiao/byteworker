import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from datetime import datetime, timezone
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_state import (  # noqa: E402
    LEGACY_STATE_SCHEMA,
    STATE_SCHEMA,
    DreamingError,
    atomic_write_json,
    empty_state,
    load_state_unlocked,
    save_state_unlocked,
    secure_path,
    state_lock,
    state_path,
    state_usage,
)


class DreamingStateTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        (kb / ".git" / "info" / "exclude").write_text(
            "# local excludes\n",
            encoding="utf-8",
        )
        return kb

    def legacy_state(self, now: datetime) -> dict:
        value = empty_state(now)
        value["schema_version"] = LEGACY_STATE_SCHEMA
        value.pop("state_revision")
        value.pop("grants")
        value.pop("runs")
        value.pop("cursors")
        value.pop("gaps")
        value.pop("receipt_index")
        value.pop("capability_tour_version")
        value.pop("capability_tour_acknowledged_at")
        value["jobs"].pop("maintenance")
        for job in value["jobs"].values():
            for field in (
                "next_attempt_at",
                "consecutive_failures",
                "deadline_at",
                "blocked_by",
                "ready_since",
            ):
                job.pop(field)
        value["enabled"] = True
        value["owner_harness"] = "trae"
        return value

    def test_missing_state_is_read_only(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            value = load_state_unlocked(kb, now)
            self.assertEqual(STATE_SCHEMA, value["schema_version"])
            self.assertFalse(state_path(kb).exists())
            self.assertFalse(state_path(kb).parent.exists())

    def test_layout_and_files_use_private_permissions(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            with state_lock(kb):
                save_state_unlocked(kb, empty_state(now))

            root_mode = stat.S_IMODE(state_path(kb).parent.stat().st_mode)
            file_mode = stat.S_IMODE(state_path(kb).stat().st_mode)
            lock_mode = stat.S_IMODE(
                (state_path(kb).parent / "state.lock").stat().st_mode
            )
            self.assertEqual(0o700, root_mode)
            self.assertEqual(0o600, file_mode)
            self.assertEqual(0o600, lock_mode)
            self.assertIn(
                "/state/",
                (kb / ".git" / "info" / "exclude").read_text(encoding="utf-8"),
            )
            usage = state_usage(kb)
            self.assertGreaterEqual(usage["files"], 2)
            self.assertGreater(usage["bytes"], 0)

    def test_usage_is_zero_before_state_exists(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            self.assertEqual({"files": 0, "bytes": 0}, state_usage(kb))

    def test_v1_migrates_with_private_backup_and_preserves_history(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, 1, 2, 3, tzinfo=timezone.utc)
            legacy = self.legacy_state(now)
            with state_lock(kb):
                atomic_write_json(state_path(kb), legacy)
                migrated = load_state_unlocked(kb, now)

            self.assertEqual(STATE_SCHEMA, migrated["schema_version"])
            self.assertEqual(LEGACY_STATE_SCHEMA, migrated["migrated_from"])
            self.assertTrue(migrated["enabled"])
            self.assertEqual("trae", migrated["owner_harness"])
            self.assertEqual("off", migrated["grants"]["im"]["mode"])
            self.assertIn("next_attempt_at", migrated["jobs"]["process"])
            backups = list(
                (state_path(kb).parent / "migrations").glob("state-v1-*.json")
            )
            self.assertEqual(1, len(backups))
            self.assertEqual(0o600, stat.S_IMODE(backups[0].stat().st_mode))
            self.assertEqual(
                LEGACY_STATE_SCHEMA,
                json.loads(backups[0].read_text(encoding="utf-8"))[
                    "schema_version"
                ],
            )

    def test_existing_v2_adds_disabled_maintenance_job_in_memory(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            value = empty_state(now)
            value["jobs"].pop("maintenance")
            value.pop("capability_tour_version")
            value.pop("capability_tour_acknowledged_at")
            with state_lock(kb):
                atomic_write_json(state_path(kb), value)
                loaded = load_state_unlocked(kb, now)

            self.assertFalse(loaded["jobs"]["maintenance"]["enabled"])
            self.assertEqual(
                {"kind": "weekday_time", "time": "03:30"},
                loaded["jobs"]["maintenance"]["schedule"],
            )
            self.assertEqual("", loaded["capability_tour_version"])
            self.assertIsNone(loaded["capability_tour_acknowledged_at"])
            persisted = json.loads(state_path(kb).read_text(encoding="utf-8"))
            self.assertNotIn("maintenance", persisted["jobs"])

    def test_failed_migration_write_does_not_replace_v1(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            legacy = self.legacy_state(now)
            with state_lock(kb):
                atomic_write_json(state_path(kb), legacy)
                real_write = atomic_write_json

                def fail_state_write(path, value):
                    if path == state_path(kb) and value.get("schema_version") == STATE_SCHEMA:
                        raise OSError("injected")
                    return real_write(path, value)

                with mock.patch(
                    "dreaming_state.atomic_write_json",
                    side_effect=fail_state_write,
                ):
                    with self.assertRaises(DreamingError) as caught:
                        load_state_unlocked(kb, now)

            self.assertEqual(
                "DREAMING_STATE_MIGRATION_FAILED",
                caught.exception.code,
            )
            self.assertEqual(
                LEGACY_STATE_SCHEMA,
                json.loads(state_path(kb).read_text(encoding="utf-8"))[
                    "schema_version"
                ],
            )

    def test_unknown_schema_and_non_object_fail_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            for payload in (
                {"schema_version": "byteworker-dreaming/v999"},
                ["not", "an", "object"],
            ):
                with self.subTest(payload=payload):
                    with state_lock(kb):
                        state_path(kb).write_text(
                            json.dumps(payload),
                            encoding="utf-8",
                        )
                        os.chmod(state_path(kb), 0o600)
                        with self.assertRaises(DreamingError):
                            load_state_unlocked(kb, now)

    def test_malformed_v2_fails_closed(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            malformed = empty_state(now)
            malformed["grants"]["im"]["mode"] = "everything"
            with state_lock(kb):
                atomic_write_json(state_path(kb), malformed)
                with self.assertRaises(DreamingError) as caught:
                    load_state_unlocked(kb, now)
            self.assertEqual("DREAMING_STATE_INVALID", caught.exception.code)

    def test_secure_path_rejects_escape_and_absolute_path(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            for part in ("../secret", "/tmp/secret"):
                with self.subTest(part=part):
                    with self.assertRaises(DreamingError) as caught:
                        secure_path(kb, part)
                    self.assertEqual(
                        "DREAMING_STATE_PATH_INVALID",
                        caught.exception.code,
                    )

    def test_state_symlink_is_rejected_before_touching_target(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = self.make_kb(root)
            external = root / "external"
            external.mkdir()
            (kb / "state").symlink_to(external, target_is_directory=True)
            original_mode = stat.S_IMODE(external.stat().st_mode)
            with self.assertRaises(DreamingError) as caught:
                with state_lock(kb):
                    pass
            self.assertEqual(
                "DREAMING_STATE_PATH_INVALID",
                caught.exception.code,
            )
            self.assertEqual(original_mode, stat.S_IMODE(external.stat().st_mode))
            self.assertEqual([], list(external.iterdir()))

    def test_save_refuses_legacy_schema(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            with state_lock(kb):
                with self.assertRaises(DreamingError) as caught:
                    save_state_unlocked(kb, self.legacy_state(now))
            self.assertEqual("DREAMING_STATE_INVALID", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
