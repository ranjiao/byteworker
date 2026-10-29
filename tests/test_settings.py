import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from settings import settings_view, update_settings, viewer_access_token_required


class SettingsFacadeTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        (kb / "context.md").write_text("时区: Asia/Shanghai\n", encoding="utf-8")
        return kb

    def test_unrelated_large_files_and_symlinks_are_not_staged(self):
        import settings
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kb = self.make_kb(root)
            (kb / "raw_data").mkdir()
            large = kb / "raw_data/unrelated.bin"
            with large.open("wb") as handle:
                handle.truncate(2 * 1024**3)  # Sparse fixture: no GB allocation/read.
            (kb / "knowledge").symlink_to(kb / "raw_data", target_is_directory=True)
            (kb / "unrelated-link").symlink_to(root / "missing")
            (kb / "state/dreaming").mkdir(parents=True)
            (kb / "state/dreaming/spool").symlink_to(kb / "raw_data", target_is_directory=True)
            real_read = Path.read_bytes
            def guarded_read(path):
                if path == large or "raw_data" in path.parts or "knowledge" in path.parts:
                    self.fail(f"unrelated content was read: {path}")
                return real_read(path)
            stage = settings._stage_settings
            def inspect_stage(live, staged, patch, now):
                snapshots = stage(live, staged, patch, now)
                for name in ("raw_data", "knowledge", "unrelated-link", ".git/objects",
                             "state/dreaming/spool"):
                    self.assertFalse((staged / name).exists(), name)
                    self.assertFalse((staged / name).is_symlink(), name)
                return snapshots
            with mock.patch.object(Path, "read_bytes", guarded_read), \
                 mock.patch("settings._stage_settings", side_effect=inspect_stage), \
                 mock.patch("shutil.copytree", side_effect=AssertionError("no tree copy")):
                result = update_settings(kb, {"viewer": {"access_token_required": False},
                                               "dreaming": {"timezone": "UTC"}})
                # Renewing a non-revoking grant must also leave spool untouched.
                renewed = update_settings(kb, {"dreaming": {"im": {"mode": "monitored"}}})
            self.assertEqual("monitored", renewed["dreaming"]["im"]["mode"])
            self.assertFalse(result["viewer"]["access_token_required"])
            self.assertEqual(2 * 1024**3, large.stat().st_size)
            self.assertTrue((kb / "knowledge").is_symlink())

    def test_staged_setting_symlink_is_rejected_without_changing_target(self):
        from settings import SettingsError
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kb = self.make_kb(root)
            (kb / "state").mkdir()
            target = root / "private.json"
            target.write_bytes(b"private")
            (kb / "state/viewer.json").symlink_to(target)
            with self.assertRaises(SettingsError):
                update_settings(kb, {"viewer": {"access_token_required": False}})
            self.assertEqual(b"private", target.read_bytes())

    def test_migration_stages_new_backup_without_copying_old_backups(self):
        from datetime import datetime, timezone
        from test_dreaming_state import DreamingStateTests
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            root = kb / "state/dreaming"
            root.mkdir(parents=True)
            legacy = DreamingStateTests().legacy_state(datetime.now(timezone.utc))
            legacy["enabled"] = False
            original = json.dumps(legacy)
            (root / "state.json").write_text(original)
            (root / "migrations").mkdir()
            (root / "migrations/unrelated-link").symlink_to(kb / "missing")
            result = update_settings(kb, {"dreaming": {"timezone": "UTC"}})
            self.assertEqual("UTC", result["dreaming"]["timezone"])
            backups = list((root / "migrations").glob("state-v1-*.json"))
            self.assertEqual(1, len(backups))
            self.assertEqual(legacy, json.loads(backups[0].read_text()))
            self.assertTrue((root / "migrations/unrelated-link").is_symlink())

    def test_invalid_later_patch_writes_nothing(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            for later in ({"dreaming": {"timezone": "UTC", "im": {"mode": "all_visible"}}},
                          {"sources": [{"source_uid": "missing"}]}):
                with self.assertRaises(Exception):
                    update_settings(kb, {"viewer": {"access_token_required": False}, **later})
                self.assertFalse((kb / "state/viewer.json").exists())
                self.assertFalse((kb / "state/dreaming/state.json").exists())

    def test_publish_failure_rolls_back_written_files(self):
        import settings
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            real_write = settings.atomic_write
            calls = []
            def failing_write(path, payload):
                calls.append(path)
                if len(calls) == 2:
                    raise OSError("disk full")
                return real_write(path, payload)
            with mock.patch("settings.atomic_write", side_effect=failing_write):
                with self.assertRaises(OSError):
                    update_settings(kb, {"viewer": {"access_token_required": False},
                                         "dreaming": {"timezone": "UTC"}})
            self.assertGreaterEqual(len(calls), 2)
            self.assertFalse((kb / "state/viewer.json").exists())
            self.assertFalse((kb / "state/dreaming/state.json").exists())

    def test_rollback_restores_existing_state_and_revoked_files(self):
        import settings
        from dreaming_grants import set_im_grant
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            update_settings(kb, {"dreaming": {"timezone": "UTC"},
                                 "viewer": {"access_token_required": True}})
            set_im_grant(kb, mode="all_visible", persist_finding=True,
                         acknowledge_all_visible=True)
            private_file = kb / "state/dreaming/spool/batch/message.json"
            private_file.parent.mkdir(parents=True)
            private_file.write_bytes(b"private fixture")
            state_file = kb / "state/dreaming/state.json"
            state = json.loads(state_file.read_text())
            state["actions"] = {"A-test": {"status": "planned"}}
            state_file.write_text(json.dumps(state))
            action_file = kb / "state/dreaming/actions/A-test.json"
            action_file.parent.mkdir()
            action_file.write_text(json.dumps({"schema_version": "byteworker-action-ledger/v1",
                                               "action_id": "A-test", "status": "planned"}))
            history = kb / "state/dreaming/finding-history.jsonl"
            history.write_text(json.dumps({"event_id": "FE-test", "batch_id": "batch",
                                           "operation": "upsert", "proposal": {}}) + "\n")
            findings = kb / "state/dreaming/findings.json"
            findings.write_text('{"findings": {"F-test": {}}}')
            targets = [state_file, kb / "state/viewer.json", private_file,
                       action_file, history, findings]
            before = {path: path.read_bytes() for path in targets}
            real_write, failed = settings.atomic_write, False
            def failing_write(path, payload):
                nonlocal failed
                if path.name == "viewer.json" and not failed:
                    failed = True
                    raise OSError("disk full")
                return real_write(path, payload)
            with mock.patch("settings.atomic_write", side_effect=failing_write):
                with self.assertRaises(OSError):
                    update_settings(kb, {"dreaming": {"im": {"mode": "off"}},
                                         "viewer": {"access_token_required": False}})
            self.assertTrue(failed)
            self.assertEqual(before, {path: path.read_bytes() for path in targets})
            update_settings(kb, {"dreaming": {"im": {"mode": "off"}}})
            self.assertFalse(private_file.exists())
            self.assertFalse(history.exists())
            self.assertEqual({}, json.loads(findings.read_text())["findings"])
            self.assertEqual("cancelled", json.loads(action_file.read_text())["status"])


    def test_all_source_profiles_validate_before_live_changes(self):
        from test_source_profiles import profile
        from source_profiles import save_profile, profile_relative_path
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            def git(*args):
                return subprocess.run(["git", *args], cwd=kb, check=True,
                                      capture_output=True, text=True).stdout.strip()
            git("init")
            git("config", "user.email", "test@example.invalid")
            git("config", "user.name", "Test")
            (kb / "knowledge").mkdir()
            (kb / "INDEX.md").write_text("# Index\n")
            git("add", "INDEX.md", "context.md")
            git("commit", "-qm", "init")
            first, second = profile(sheet_id=1), profile(sheet_id=2)
            save_profile(kb, first, skill_root=ROOT)
            save_profile(kb, second, skill_root=ROOT)
            (kb / "raw_data").mkdir()
            large = kb / "raw_data/unrelated.bin"
            with large.open("wb") as handle:
                handle.truncate(2 * 1024**3)
            (kb / "knowledge/unrelated-link").symlink_to(large)
            (kb / ".git/objects/unrelated-large.bin").symlink_to(large)
            git("pack-refs", "--all")
            baseline = git("rev-parse", "HEAD")
            original = (kb / profile_relative_path(first)).read_bytes()
            patch = {"viewer": {"access_token_required": False}, "sources": [
                {"source_uid": first["source_uid"], "routine": {"enabled": False}},
                {"source_uid": second["source_uid"],
                 "routine": {"enabled": True, "cadence": "invalid"}},
            ]}
            with self.assertRaises(Exception):
                update_settings(kb, patch)
            self.assertEqual(baseline, git("rev-parse", "HEAD"))
            self.assertEqual(original, (kb / profile_relative_path(first)).read_bytes())
            self.assertFalse((kb / "state/viewer.json").exists())
            patch["sources"][1]["routine"]["cadence"] = "daily"
            import settings
            stage = settings._stage_settings
            def inspect_stage(live, staged, patch, now):
                snapshots = stage(live, staged, patch, now)
                self.assertNotIn(Path("raw_data/unrelated.bin"), snapshots)
                self.assertNotIn(Path(".git/objects/unrelated-large.bin"), snapshots)
                self.assertTrue((staged / "raw_data").is_symlink())
                self.assertTrue((staged / "knowledge").is_symlink())
                self.assertFalse((staged / ".git/objects/unrelated-large.bin").exists())
                self.assertFalse((staged / ".git/packed-refs").exists())
                return snapshots
            targets = [kb / profile_relative_path(first), kb / profile_relative_path(second),
                       kb / "INDEX.md", kb / ".git/index", kb / ".git/HEAD",
                       kb / ".git" / git("symbolic-ref", "HEAD"), kb / ".git/logs/HEAD"]
            targets += list((kb / "journal").rglob("*.md"))
            baseline_files = {path: path.read_bytes() if path.exists() else None for path in targets}
            objects = set((kb / ".git/objects").rglob("*"))
            real_write = settings.atomic_write
            failed = False
            def failing_write(path, payload):
                nonlocal failed
                if path.name == "viewer.json" and not failed:
                    failed = True
                    raise OSError("disk full")
                return real_write(path, payload)
            with mock.patch("settings.atomic_write", side_effect=failing_write):
                with self.assertRaises(OSError):
                    update_settings(kb, patch)
            self.assertTrue(failed)
            self.assertEqual(baseline, git("rev-parse", "HEAD"))
            self.assertEqual(baseline_files,
                             {path: path.read_bytes() if path.exists() else None for path in targets})
            self.assertFalse(any(path.is_file() for path in
                                 set((kb / ".git/objects").rglob("*")) - objects))
            with mock.patch("settings._stage_settings", side_effect=inspect_stage):
                result = update_settings(kb, patch)
            self.assertEqual(str(kb), result["kb"]["path"])
            self.assertFalse(result["viewer"]["access_token_required"])
            self.assertNotEqual(baseline, git("rev-parse", "HEAD"))
            self.assertEqual(2, len(result["sources"]))

    def test_settings_view_collects_current_truth_sources(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            view = settings_view(kb)
            self.assertEqual("byteworker-settings/v1", view["schema_version"])
            self.assertTrue(view["viewer"]["access_token_required"])
            self.assertTrue(view["dreaming"]["available"])
            self.assertEqual("Asia/Shanghai", view["kb"]["context"]["timezone_hint"])
            self.assertFalse(view["report_automation"]["editable_in_viewer"])
            self.assertEqual([], view["sources"])

    def test_update_settings_persists_viewer_access_preference(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            self.assertTrue(viewer_access_token_required(kb))
            updated = update_settings(
                kb,
                {
                    "viewer": {"access_token_required": False},
                    "sources": [],
                },
            )
            self.assertFalse(updated["viewer"]["access_token_required"])
            self.assertFalse(viewer_access_token_required(kb))

    def test_update_settings_routes_to_dreaming_state(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            updated = update_settings(
                kb,
                {
                    "dreaming": {
                        "timezone": "Asia/Shanghai",
                        "jobs": {
                            "process": {
                                "enabled": True,
                                "schedule": {"kind": "interval", "minutes": 90},
                            },
                            "morning": {
                                "enabled": True,
                                "schedule": {"time": "08:45"},
                            },
                            "maintenance": {
                                "enabled": True,
                                "schedule": {"time": "03:15"},
                            },
                            "recovery": {
                                "enabled": False,
                                "schedule": {"minutes": 300},
                            },
                        },
                        "im": {
                            "mode": "monitored",
                            "persist_finding": True,
                        },
                        "logging": {"retention_days": 21},
                        "delivery": {
                            "lark_summary_enabled": False,
                            "lark_recipient_id": "",
                        },
                        "harness_preferences": {
                            "wake_interval_minutes": 120,
                            "model": "gpt-5.5",
                        },
                    },
                    "sources": [],
                },
            )
            dreaming = updated["dreaming"]
            self.assertEqual("Asia/Shanghai", dreaming["timezone"])
            self.assertEqual(
                {"kind": "interval", "minutes": 90},
                dreaming["jobs"]["process"]["schedule"],
            )
            self.assertEqual("08:45", dreaming["jobs"]["morning"]["schedule"]["time"])
            self.assertEqual("monitored", dreaming["im"]["mode"])
            self.assertTrue(dreaming["im"]["persist_finding"])
            self.assertEqual(21, dreaming["logging"]["retention_days"])
            self.assertEqual(
                {
                    "wake_interval_minutes": 120,
                    "model": "gpt-5.5",
                    "schedule_managed_by_viewer": False,
                },
                dreaming["harness_preferences"],
            )

    @mock.patch("settings.resolve_lark_recipient")
    def test_username_recipient_is_resolved_but_remains_user_facing(self, resolve):
        resolve.return_value = {
            "recipient_id": "ou_ranjiao",
            "recipient_key": "ranjiao",
            "display_name": "冉娇",
        }
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            updated = update_settings(
                kb,
                {
                    "dreaming": {
                        "timezone": "Asia/Shanghai",
                        "delivery": {
                            "lark_summary_enabled": True,
                            "lark_recipient": "ranjiao",
                        },
                    }
                },
            )
        delivery = updated["dreaming"]["delivery"]
        self.assertEqual("ranjiao", delivery["lark_recipient"])
        self.assertEqual("ou_ranjiao", delivery["lark_recipient_id"])
        resolve.assert_called_once_with("ranjiao")


class ViewerSettingsApiTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        return kb

    def free_port(self) -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def request_json(self, url: str, *, token: str = "", body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="GET" if data is None else "PATCH")
        if token:
            request.add_header("X-Byteworker-Token", token)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_viewer_settings_api_requires_token_and_updates(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            serve_root = root / "serve"
            serve_root.mkdir()
            kb = self.make_kb(root)
            port = self.free_port()
            token = "test-token"
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "bin" / "viewer-server.py"),
                    "--root",
                    str(serve_root),
                    "--kb",
                    str(kb),
                    "--port",
                    str(port),
                    "--token",
                    token,
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                url = f"http://127.0.0.1:{port}/api/settings"
                deadline = time.time() + 5
                while True:
                    try:
                        self.request_json(url, token=token)
                        break
                    except Exception:
                        if time.time() > deadline:
                            raise
                        time.sleep(0.05)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request_json(url)
                self.assertEqual(403, caught.exception.code)
                updated = self.request_json(
                    url,
                    token=token,
                    body={
                        "viewer": {"access_token_required": True},
                        "dreaming": {
                            "timezone": "Asia/Shanghai",
                            "jobs": {
                                "process": {
                                    "enabled": True,
                                    "schedule": {
                                        "kind": "daily_time",
                                        "time": "21:30",
                                    },
                                },
                            },
                            "harness_preferences": {
                                "wake_interval_minutes": 240,
                                "model": "gpt-5.5",
                            },
                        },
                        "sources": [],
                    },
                )
                self.assertTrue(updated["viewer"]["access_token_required"])
                self.assertTrue(updated["viewer"]["session_access_token_required"])
                self.assertEqual(
                    {"kind": "daily_time", "time": "21:30"},
                    updated["dreaming"]["jobs"]["process"]["schedule"],
                )
                self.assertEqual(
                    240,
                    updated["dreaming"]["harness_preferences"][
                        "wake_interval_minutes"
                    ],
                )
                self.assertEqual(
                    "gpt-5.5",
                    updated["dreaming"]["harness_preferences"]["model"],
                )
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def test_viewer_settings_api_can_run_without_access_token(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            serve_root = root / "serve"
            serve_root.mkdir()
            kb = self.make_kb(root)
            port = self.free_port()
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(ROOT / "bin" / "viewer-server.py"),
                    "--root",
                    str(serve_root),
                    "--kb",
                    str(kb),
                    "--port",
                    str(port),
                    "--auth-mode",
                    "none",
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                url = f"http://127.0.0.1:{port}/api/settings"
                deadline = time.time() + 5
                while True:
                    try:
                        payload = self.request_json(url)
                        break
                    except Exception:
                        if time.time() > deadline:
                            raise
                        time.sleep(0.05)
                self.assertFalse(payload["viewer"]["session_access_token_required"])
                updated = self.request_json(
                    url,
                    body={
                        "viewer": {"access_token_required": False},
                        "sources": [],
                    },
                )
                self.assertFalse(updated["viewer"]["access_token_required"])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
