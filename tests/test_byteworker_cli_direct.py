import importlib.util
import io
import json
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


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
        self.assertEqual({"ok": True}, CLI._parse_json('{"ok":true}'))
        self.assertEqual("text", CLI._parse_json("text"))
        self.assertIsNone(CLI._parse_json(""))
        self.assertTrue(CLI._bounded("x" * 3000).endswith("…"))
        with self.assertRaises(CLI.ProtocolUsageError):
            CLI.parser().parse_args([])

    def test_run_tool_success_attention_and_structured_error(self):
        success = subprocess.CompletedProcess(
            ["tool"],
            0,
            stdout='{"value":1}\n',
            stderr="",
        )
        output = io.StringIO()
        with (
            patch.object(CLI.subprocess, "run", return_value=success),
            redirect_stdout(output),
        ):
            code = CLI._run_tool("wiki", ["auth-status"], pretty=False)
        payload = json.loads(output.getvalue())
        self.assertEqual(0, code)
        self.assertEqual("success", payload["status"])
        self.assertEqual(1, payload["data"]["value"])

        attention = subprocess.CompletedProcess(
            ["tool"],
            2,
            stdout='{"findings":[]}\n',
            stderr="",
        )
        output = io.StringIO()
        with (
            patch.object(CLI.subprocess, "run", return_value=attention) as run,
            redirect_stdout(output),
        ):
            CLI._run_tool("doctor", ["scan", "--kb", "/tmp"], pretty=True)
        self.assertEqual("attention", json.loads(output.getvalue())["status"])
        self.assertIn("--format", run.call_args.args[0])

        failure = subprocess.CompletedProcess(
            ["tool"],
            1,
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
            patch.object(CLI.subprocess, "run", return_value=failure),
            redirect_stdout(output),
        ):
            CLI._run_tool("wiki", ["inspect"], pretty=False)
        error = json.loads(output.getvalue())["error"]
        self.assertEqual("WIKI_PERMISSION_DENIED", error["code"])
        self.assertEqual("share it", error["hint"])

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

        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, CLI.main(["update-status"]))
        self.assertEqual("success", json.loads(output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
