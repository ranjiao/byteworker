import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_bin_module(filename: str, module_name: str):
    path = ROOT / "bin" / filename
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


update_state_cli = load_bin_module(
    "update-state.py",
    "byteworker_update_state_cli_test",
)
update_postflight_cli = load_bin_module(
    "update-postflight.py",
    "byteworker_update_postflight_cli_test",
)


class UpdateStateCliTests(unittest.TestCase):
    def test_state_lifecycle_and_due_exit_codes(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            state = Path(temporary) / "state.json"
            base = ["--state", str(state), "--now", "100"]

            self.assertEqual(0, update_state_cli.main([*base, "attempt"]))
            self.assertEqual(
                0,
                update_state_cli.main(
                    [
                        "--state",
                        str(state),
                        "--now",
                        "101",
                        "success",
                        "--commit",
                        "abc123",
                    ]
                ),
            )
            self.assertEqual(
                10,
                update_state_cli.main(
                    ["--state", str(state), "--now", "102", "due"]
                ),
            )
            self.assertEqual(
                0,
                update_state_cli.main(
                    ["--state", str(state), "--now", "102", "due", "--force"]
                ),
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(
                    0,
                    update_state_cli.main(
                        ["--state", str(state), "--now", "102", "status"]
                    ),
                )
            payload = json.loads(output.getvalue())
            self.assertEqual("abc123", payload["last_checked_commit"])
            self.assertEqual(101, payload["last_success_at"])

    def test_postflight_retry_state_round_trip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            state = Path(temporary) / "state.json"
            common = ["--state", str(state), "--now", "200"]
            self.assertEqual(
                0,
                update_state_cli.main(
                    [*common, "postflight-pending", "--commit", "def456"]
                ),
            )
            self.assertEqual(
                0,
                update_state_cli.main(
                    [*common, "postflight-failure", "--code", "exit-2"]
                ),
            )
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertTrue(payload["postflight_pending"])
            self.assertEqual("def456", payload["postflight_commit"])
            self.assertEqual(1, payload["postflight_failure_count"])
            self.assertEqual("exit-2", payload["postflight_last_failure_code"])
            self.assertEqual(
                10,
                update_state_cli.main(
                    ["--state", str(state), "--now", "499", "postflight-due"]
                ),
            )
            self.assertEqual(
                0,
                update_state_cli.main(
                    ["--state", str(state), "--now", "500", "postflight-due"]
                ),
            )
            self.assertEqual(
                0,
                update_state_cli.main(
                    ["--state", str(state), "--now", "500", "postflight-success"]
                ),
            )
            payload = json.loads(state.read_text(encoding="utf-8"))
            self.assertFalse(payload["postflight_pending"])
            self.assertEqual(0, payload["postflight_failure_count"])


class UpdatePostflightCliTests(unittest.TestCase):
    def test_missing_kb_and_bounded_exception_are_actionable_json(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            missing = Path(temporary) / "missing"
            output = io.StringIO()
            with redirect_stdout(output):
                code = update_postflight_cli.main(
                    ["--kb", str(missing), "--format", "json"]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(2, code)
            self.assertEqual("decision", payload["status"])
            self.assertIn("知识库目录不存在", payload["message"])

            kb = Path(temporary) / "kb"
            kb.mkdir()
            output = io.StringIO()
            with (
                mock.patch.object(
                    update_postflight_cli,
                    "run_postflight",
                    side_effect=RuntimeError("x" * 400),
                ),
                redirect_stdout(output),
            ):
                code = update_postflight_cli.main(
                    ["--kb", str(kb), "--format", "json"]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(2, code)
            self.assertEqual("decision", payload["status"])
            self.assertIn("执行失败", payload["message"])
            self.assertLessEqual(len(payload["message"]), 230)

    def test_success_and_decision_status_control_exit_code(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            for status, expected in (("healthy", 0), ("notice", 0), ("decision", 2)):
                with self.subTest(status=status):
                    result = SimpleNamespace(
                        status=status,
                        to_dict=lambda status=status: {"status": status},
                    )
                    output = io.StringIO()
                    with (
                        mock.patch.object(
                            update_postflight_cli,
                            "run_postflight",
                            return_value=result,
                        ),
                        mock.patch.object(
                            update_postflight_cli,
                            "render_message",
                            return_value=f"doctor:{status}",
                        ),
                        redirect_stdout(output),
                    ):
                        code = update_postflight_cli.main(
                            ["--kb", str(kb), "--format", "json"]
                        )
                    payload = json.loads(output.getvalue())
                    self.assertEqual(expected, code)
                    self.assertEqual(status, payload["status"])
                    self.assertEqual(f"doctor:{status}", payload["message"])


class ProvenanceBackfillCliTests(unittest.TestCase):
    def test_direct_cli_audit_plan_and_skill_path_rejection(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            for relative in ("raw_data", "provenance", "knowledge/readings"):
                (kb / relative).mkdir(parents=True, exist_ok=True)

            audit = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "provenance-backfill.py"),
                    "audit",
                    "--kb",
                    str(kb),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, audit.returncode, audit.stderr)
            audit_payload = json.loads(audit.stdout)
            self.assertEqual("ok", audit_payload["status"])
            self.assertEqual("read-only", audit_payload["mode"])

            plan_path = root / "backfill-plan.json"
            planned = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "provenance-backfill.py"),
                    "plan",
                    "--kb",
                    str(kb),
                    "--output",
                    str(plan_path),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, planned.returncode, planned.stderr)
            planned_payload = json.loads(planned.stdout)
            self.assertEqual("planned", planned_payload["status"])
            self.assertFalse(planned_payload["applied"])
            self.assertTrue(plan_path.is_file())

            rejected = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "provenance-backfill.py"),
                    "plan",
                    "--kb",
                    str(kb),
                    "--output",
                    str(ROOT / ".tmp-backfill-plan.json"),
                ],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(2, rejected.returncode)
            self.assertIn("不能写入 skill 仓库", rejected.stdout)
            self.assertFalse((ROOT / ".tmp-backfill-plan.json").exists())


if __name__ == "__main__":
    unittest.main()
