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
