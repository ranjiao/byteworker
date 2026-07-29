import json
import subprocess
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "byteworker-cli.py"


class MachineProtocolTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            ["python3", str(CLI), *map(str, args)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_todo_success_uses_stable_single_line_envelope(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            result = self.run_cli(
                "todo",
                kb,
                "init",
                "--template",
                ROOT / "templates" / "todo.md",
            )
            self.assertEqual(0, result.returncode)
            self.assertEqual("", result.stderr)
            self.assertEqual(1, len(result.stdout.splitlines()))
            payload = json.loads(result.stdout)
            self.assertEqual("success", payload["status"])
            self.assertIsNone(payload["error"])
            self.assertEqual("byteworker-cli/v1", payload["context"]["protocol"])
            self.assertEqual("todo", payload["context"]["tool"])
            self.assertEqual("init", payload["context"]["operation"])
            self.assertTrue(payload["data"]["created"])

    def test_argument_error_has_stable_code_and_exit_status(self):
        result = self.run_cli("kb-query", "search", "--kb", "/tmp/missing-query")
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("error", payload["status"])
        self.assertIsNone(payload["data"])
        self.assertEqual("KB_QUERY_INPUT_ERROR", payload["error"]["code"])
        self.assertEqual(2, payload["error"]["details"]["exit_code"])

    def test_facade_usage_error_is_also_an_envelope(self):
        result = self.run_cli("unknown-tool")
        self.assertEqual(2, result.returncode)
        self.assertEqual("", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual("error", payload["status"])
        self.assertEqual("CLI_USAGE_ERROR", payload["error"]["code"])
        self.assertEqual("cli", payload["context"]["tool"])

    def test_update_status_uses_same_envelope(self):
        result = self.run_cli("update-status")
        self.assertEqual(0, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual(1, payload["data"]["version"])
        self.assertEqual("update-status", payload["context"]["tool"])

    def test_doctor_findings_are_attention_not_transport_error(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            result = self.run_cli("doctor", "scan", "--kb", temporary)
        self.assertEqual(2, result.returncode)
        payload = json.loads(result.stdout)
        self.assertEqual("attention", payload["status"])
        self.assertIsNone(payload["error"])
        self.assertGreater(payload["data"]["summary"]["error"], 0)
        self.assertEqual("scan", payload["context"]["operation"])


if __name__ == "__main__":
    unittest.main()
