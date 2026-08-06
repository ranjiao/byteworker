import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_run_log import append_run_event  # noqa: E402


class ViewerDreamingDebugTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        return kb

    def free_port(self) -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def request_json(self, url: str):
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_debug_page_is_standalone_and_has_valid_javascript(self):
        page = (ROOT / "viewer" / "dreaming-debug.html").read_text(encoding="utf-8")
        index = (ROOT / "viewer" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<title>Dreaming 运行日志</title>", page)
        self.assertNotIn("auto-refresh", page)
        self.assertNotIn("setInterval", page)
        self.assertIn('id="time-filter"', page)
        self.assertIn("JOB_DESCRIPTIONS", page)
        self.assertIn("补偿后台异常，例如过期租约", page)
        self.assertIn("renderRepairs", page)
        self.assertIn("openFindingIds", page)
        self.assertIn("URLSearchParams", page)
        self.assertIn("/api/dreaming/runs?", page)
        self.assertIn("options.forceDetail || !state.detail", page)
        self.assertNotIn("summary?.status === \"running\"", page)
        self.assertNotIn("dreaming-debug.html", index)

        if shutil.which("node") is None:
            self.skipTest("node is required for JavaScript syntax validation")
        script = page.split("<script>", 1)[1].split("</script>", 1)[0]
        completed = subprocess.run(
            ["node", "--check", "-"],
            input=script,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_read_only_log_api_lists_and_shows_runs_without_token(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            serve_root = root / "serve"
            serve_root.mkdir()
            (serve_root / "app").symlink_to(ROOT / "viewer", target_is_directory=True)
            kb = self.make_kb(root)
            started = datetime(2026, 8, 6, 2, 0, tzinfo=timezone.utc)
            append_run_event(
                kb,
                run_id="DR-debug-test",
                event="leased",
                job="morning",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="scheduled",
                status="running",
                now=started,
            )
            append_run_event(
                kb,
                run_id="DR-debug-test",
                event="completed",
                job="morning",
                period="2026-08-06",
                owner="test-host",
                epoch=1,
                stage="complete",
                status="success",
                metrics={"duration_ms": 120000},
                now=started + timedelta(minutes=2),
            )
            append_run_event(
                kb,
                run_id="DR-old-test",
                event="leased",
                job="process",
                period="2026-08-05",
                owner="test-host",
                epoch=1,
                stage="scheduled",
                status="running",
                now=started - timedelta(days=1),
            )
            append_run_event(
                kb,
                run_id="DR-old-test",
                event="completed",
                job="process",
                period="2026-08-05",
                owner="test-host",
                epoch=1,
                stage="complete",
                status="success",
                now=started - timedelta(days=1) + timedelta(minutes=1),
            )

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
                    "--token",
                    "settings-token",
                ],
                cwd=ROOT,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            try:
                base = f"http://127.0.0.1:{port}"
                deadline = time.time() + 5
                while True:
                    try:
                        status, runs = self.request_json(
                            f"{base}/api/dreaming/runs?limit=20"
                        )
                        break
                    except Exception:
                        if time.time() > deadline:
                            raise
                        time.sleep(0.05)

                self.assertEqual(200, status)
                self.assertEqual(2, runs["count"])
                self.assertEqual("DR-debug-test", runs["runs"][0]["run_id"])
                self.assertEqual("success", runs["runs"][0]["status"])
                self.assertEqual("DR-old-test", runs["runs"][1]["run_id"])

                since = urllib.parse.quote(
                    (started - timedelta(minutes=5)).isoformat()
                )
                _, filtered = self.request_json(
                    f"{base}/api/dreaming/runs?limit=20&since={since}"
                )
                self.assertEqual(1, filtered["count"])
                self.assertEqual("DR-debug-test", filtered["runs"][0]["run_id"])

                _, detail = self.request_json(
                    f"{base}/api/dreaming/runs/DR-debug-test"
                )
                self.assertEqual(2, detail["event_count"])
                self.assertEqual(
                    ["leased", "completed"],
                    [event["event"] for event in detail["events"]],
                )
                self.assertEqual("report", detail["result"]["kind"])
                self.assertFalse(detail["result"]["available"])

                with urllib.request.urlopen(
                    f"{base}/app/dreaming-debug.html", timeout=5
                ) as response:
                    html = response.read().decode("utf-8")
                self.assertIn("Dreaming 运行日志", html)

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.request_json(f"{base}/api/dreaming/runs?limit=bad")
                self.assertEqual(400, caught.exception.code)
            finally:
                process.terminate()
                process.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
