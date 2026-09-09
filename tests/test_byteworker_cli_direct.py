import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from bounded_process import CapturedProcess, run_bounded_output  # noqa: E402


def load_cli():
    spec = importlib.util.spec_from_file_location(
        "byteworker_facade_direct",
        ROOT / "bin" / "byteworker-cli.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


CLI = load_cli()


class ByteworkerCliDirectTests(unittest.TestCase):
    def test_helpers_and_parser(self):
        args = CLI.parser().parse_args(["wiki", "auth-status"])
        self.assertEqual("wiki", args.tool)
        self.assertEqual("auth-status", CLI._operation("wiki", args.args))
        self.assertEqual("check", CLI._operation("todo", ["kb", "check"]))
        self.assertEqual("", CLI._operation("source", []))
        self.assertEqual(
            ("source", ["--help"]),
            CLI._tool_help_request(["source", "--help"]),
        )
        self.assertEqual(
            ("source", ["-h"]),
            CLI._tool_help_request(["--pretty", "source", "-h"]),
        )
        self.assertIsNone(CLI._tool_help_request(["unknown", "--help"]))
        self.assertEqual(
            ("source", ["bundle", "--help"]),
            CLI._tool_help_request(["source", "bundle", "--help"]),
        )
        self.assertEqual({"ok": True}, CLI._parse_json('{"ok":true}'))
        self.assertEqual("text", CLI._parse_json("text"))
        self.assertIsNone(CLI._parse_json(""))
        self.assertTrue(CLI._bounded("x" * 3000).endswith("…"))
        with self.assertRaises(CLI.ProtocolUsageError):
            CLI.parser().parse_args([])

    def test_run_tool_success_attention_and_structured_error(self):
        success = CapturedProcess(
            returncode=0,
            stdout='{"value":1}\n',
            stderr="",
        )
        output = io.StringIO()
        with (
            patch.object(CLI, "run_bounded_output", return_value=success),
            redirect_stdout(output),
        ):
            code = CLI._run_tool("wiki", ["auth-status"], pretty=False)
        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("success", payload["status"])
        self.assertEqual(1, payload["data"]["value"])

        attention = CapturedProcess(
            returncode=2,
            stdout='{"findings":[]}\n',
            stderr="",
        )
        output = io.StringIO()
        with (
            patch.object(CLI, "run_bounded_output", return_value=attention) as run,
            redirect_stdout(output),
        ):
            CLI._run_tool("doctor", ["scan", "--kb", "/tmp"], pretty=True)
        self.assertEqual("attention", json.loads(output.getvalue())["status"])
        self.assertIn("--format", run.call_args.args[0])

        failure = CapturedProcess(
            returncode=1,
            stdout=json.dumps(
                {
                    "error": {
                        "code": "WIKI_PERMISSION_DENIED",
                        "message": "denied",
                        "hint": "share it",
                        "details": {"node": "x"},
                    }
                }
            ),
            stderr="trace",
        )
        output = io.StringIO()
        with (
            patch.object(CLI, "run_bounded_output", return_value=failure),
            redirect_stdout(output),
        ):
            CLI._run_tool("wiki", ["inspect"], pretty=False)
        error = json.loads(output.getvalue())["error"]
        self.assertEqual("WIKI_PERMISSION_DENIED", error["code"])
        self.assertEqual("share it", error["hint"])

    def test_large_result_returns_private_artifact_receipt(self):
        completed = run_bounded_output(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 4096)",
            ],
            inline_stdout_bytes=1024,
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIsNotNone(completed.artifact)
        artifact = completed.artifact
        path = Path(str(artifact["artifact_path"]))
        try:
            self.assertEqual("byteworker-cli-artifact/v1", artifact["protocol"])
            self.assertEqual(4096, artifact["bytes"])
            self.assertTrue(str(artifact["sha256"]).startswith("sha256:"))
            self.assertEqual("0600", artifact["mode"])
            self.assertEqual(0o600, os.stat(path).st_mode & 0o777)
            self.assertEqual(b"x" * 4096, path.read_bytes())
        finally:
            path.unlink(missing_ok=True)

    def test_stdout_and_stderr_are_drained_without_unbounded_stderr(self):
        completed = run_bounded_output(
            [
                sys.executable,
                "-c",
                (
                    "import sys; "
                    "sys.stderr.buffer.write(b'e' * 2000000); "
                    "sys.stdout.buffer.write(b'o' * 2000000)"
                ),
            ],
            inline_stdout_bytes=1024,
            stderr_bytes=2048,
        )
        self.assertEqual(0, completed.returncode)
        self.assertEqual(2048, len(completed.stderr))
        artifact = completed.artifact
        self.assertIsNotNone(artifact)
        path = Path(str(artifact["artifact_path"]))
        try:
            self.assertEqual(2_000_000, artifact["bytes"])
            self.assertEqual(2_000_000, path.stat().st_size)
        finally:
            path.unlink(missing_ok=True)

    def test_facade_exposes_large_success_as_artifact(self):
        artifact = {
            "protocol": "byteworker-cli-artifact/v1",
            "artifact_path": "/tmp/result.out",
            "bytes": 2_000_000,
            "sha256": "sha256:test",
            "content_type": "application/json",
            "mode": "0600",
            "temporary": True,
        }
        completed = CapturedProcess(
            returncode=0,
            stdout="",
            stderr="",
            artifact=artifact,
        )
        output = io.StringIO()
        with (
            patch.object(CLI, "run_bounded_output", return_value=completed),
            redirect_stdout(output),
        ):
            self.assertEqual(0, CLI._run_tool("source", ["capabilities"], pretty=False))
        payload = json.loads(output.getvalue())
        self.assertEqual("success", payload["status"])
        self.assertEqual(artifact, payload["data"])

    def test_legacy_error_and_main_paths(self):
        self.assertEqual(
            "message",
            CLI._legacy_error_message({"error": {"message": "message"}}, ""),
        )
        self.assertEqual("stderr", CLI._legacy_error_message(None, "stderr"))
        self.assertEqual("text", CLI._legacy_error_message("text", ""))
        self.assertEqual("命令执行失败", CLI._legacy_error_message(None, ""))
        code, hint = CLI._error_code("wiki", 2, "未指定 --kb")
        self.assertEqual("KB_CONFIG_MISSING", code)
        self.assertTrue(hint)
        self.assertIsNone(CLI._structured_error({"error": "bad"}))

        output = io.StringIO()
        with redirect_stdout(output):
            code = CLI.main([])
        self.assertEqual(2, code)
        self.assertEqual("CLI_USAGE_ERROR", json.loads(output.getvalue())["error"]["code"])

        output = io.StringIO()
        with (
            patch.object(CLI, "_run_tool", return_value=0) as run,
            redirect_stdout(output),
        ):
            self.assertEqual(0, CLI.main(["digest-job", "list", "--kb", "/tmp"]))
        run.assert_called_once()

        with patch.object(CLI, "_run_tool_help", return_value=0) as run_help:
            self.assertEqual(0, CLI.main(["todo", "--help"]))
        run_help.assert_called_once_with("todo", ["--help"])

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, CLI.main(["update-status"]))
        self.assertEqual("success", json.loads(output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
