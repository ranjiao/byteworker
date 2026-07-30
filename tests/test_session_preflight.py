import json
import importlib.util
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
import sys

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import session_preflight  # noqa: E402


def load_preflight_cli():
    path = ROOT / "bin" / "session-preflight.py"
    spec = importlib.util.spec_from_file_location("session_preflight_cli_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PREFLIGHT_CLI = load_preflight_cli()


def runtime(*, ready=True):
    return {
        "schema_version": "byteworker-runtime-check/v1",
        "ready": ready,
        "core_ready": ready,
        "required_sources": ["feishu"],
        "python": {
            "path": sys.executable,
            "version": "3.11.0",
            "status": "ok" if ready else "missing",
            "required": True,
            "tier": 1,
            "error": "",
        },
        "programs": {
            "git": {
                "path": "/usr/bin/git",
                "version": "git",
                "status": "ok" if ready else "missing",
                "required": True,
                "tier": 1,
                "error": "",
            }
        },
    }


class SessionPreflightTests(unittest.TestCase):
    def layout(self, root: Path):
        kb = root / "kb"
        (root / "bin").mkdir()
        kb.mkdir()
        (root / ".kbconfig").write_text(str(kb) + "\n", encoding="utf-8")
        (kb / "context.md").write_text("# context\n", encoding="utf-8")
        (kb / "todo.md").write_text("# todo\n", encoding="utf-8")
        sources = kb / "sources"
        sources.mkdir()
        (sources / "chat.json").write_text(
            json.dumps({"source_type": "feishu_chat"}),
            encoding="utf-8",
        )
        return kb

    def test_healthy_run_is_empty_of_notices_and_infers_configured_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            kb = self.layout(root)
            runner = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    ["update"], 0, stdout="", stderr=""
                )
            )
            with (
                mock.patch.object(session_preflight, "check_runtime", return_value=runtime()),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "_run_json",
                    return_value=(0, [], ""),
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": False,
                        "prompt_upgrade_available": False,
                    },
                ),
            ):
                result = session_preflight.run_preflight(root, runner=runner)
            self.assertEqual("healthy", result["status"])
            self.assertTrue(result["ready"])
            self.assertEqual([], result["notices"])
            self.assertEqual(["feishu"], result["required_sources"])
            self.assertEqual(str(kb.resolve()), result["kb"])

    def test_surfaces_update_todo_and_one_time_report_notices(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.layout(root)
            runner = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    ["update"],
                    0,
                    stdout="byteworker:自动更新暂时失败。",
                    stderr="",
                )
            )
            reminder = {
                "id": "T-20260730-001",
                "title": "提交材料",
                "category": "due_soon",
            }
            with (
                mock.patch.object(session_preflight, "check_runtime", return_value=runtime()),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "_run_json",
                    return_value=(0, [reminder], ""),
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": True,
                        "prompt_upgrade_available": False,
                    },
                ),
                mock.patch.object(session_preflight, "record_decision") as decision,
            ):
                result = session_preflight.run_preflight(root, runner=runner)
            self.assertEqual("attention", result["status"])
            self.assertTrue(result["ready"])
            self.assertEqual(
                [
                    "UPDATE_CHECK_NOTICE",
                    "TODO_REMINDERS",
                    "REPORT_AUTOMATION_ONBOARDING",
                ],
                [item["code"] for item in result["notices"]],
            )
            todo_notice = result["notices"][1]["data"]["items"][0]
            self.assertEqual(
                {"id", "title", "category"},
                set(todo_notice),
            )
            decision.assert_called_once_with(
                (Path(temporary) / "kb").resolve(),
                decision="prompted",
            )

    def test_todo_command_failure_is_bounded_into_a_notice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.layout(root)
            with (
                mock.patch.object(
                    session_preflight,
                    "check_runtime",
                    return_value=runtime(),
                ),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": False,
                        "prompt_upgrade_available": False,
                    },
                ),
            ):
                result = session_preflight.run_preflight(
                    root,
                    skip_update=True,
                    runner=mock.Mock(side_effect=OSError("cannot execute")),
                )
            self.assertFalse(result["ready"])
            notice = next(
                item
                for item in result["notices"]
                if item["code"] == "TODO_CHECK_FAILED"
            )
            self.assertIn("cannot execute", notice["message"])

    def test_update_launch_failure_is_nonblocking_and_bounded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.layout(root)
            with (
                mock.patch.object(
                    session_preflight,
                    "check_runtime",
                    return_value=runtime(),
                ),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "_run_json",
                    return_value=(0, [], ""),
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": False,
                        "prompt_upgrade_available": False,
                    },
                ),
            ):
                result = session_preflight.run_preflight(
                    root,
                    runner=mock.Mock(side_effect=OSError("cannot launch")),
                )
            self.assertTrue(result["ready"])
            self.assertEqual(
                "UPDATE_CHECK_NOTICE",
                result["notices"][0]["code"],
            )
            self.assertIn("cannot launch", result["notices"][0]["message"])

    def test_report_onboarding_write_failure_is_bounded_and_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.layout(root)
            with (
                mock.patch.object(
                    session_preflight,
                    "check_runtime",
                    return_value=runtime(),
                ),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "_run_json",
                    return_value=(0, [], ""),
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": True,
                        "prompt_upgrade_available": False,
                    },
                ),
                mock.patch.object(
                    session_preflight,
                    "record_decision",
                    side_effect=OSError("read only"),
                ),
            ):
                result = session_preflight.run_preflight(
                    root,
                    skip_update=True,
                )
            self.assertFalse(result["ready"])
            self.assertEqual(
                "REPORT_AUTOMATION_STATE_INVALID",
                result["notices"][-1]["code"],
            )

    def test_missing_runtime_blocks_before_business_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.layout(root)
            with (
                mock.patch.object(
                    session_preflight,
                    "check_runtime",
                    return_value=runtime(ready=False),
                ),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
                mock.patch.object(
                    session_preflight,
                    "report_status",
                    return_value={
                        "needs_onboarding": False,
                        "prompt_upgrade_available": False,
                    },
                ),
            ):
                result = session_preflight.run_preflight(
                    root,
                    skip_update=True,
                )
            self.assertFalse(result["ready"])
            self.assertEqual(
                "RUNTIME_DEPENDENCY_INVALID",
                result["notices"][0]["code"],
            )

    def test_first_use_still_runs_update_before_returning_onboarding_notice(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bin").mkdir()
            runner = mock.Mock(
                return_value=subprocess.CompletedProcess(
                    ["update"], 0, stdout="", stderr=""
                )
            )
            with (
                mock.patch.object(
                    session_preflight,
                    "check_runtime",
                    return_value=runtime(),
                ),
                mock.patch.object(
                    session_preflight,
                    "runtime_environment",
                    return_value={"PATH": "/usr/bin"},
                ),
            ):
                result = session_preflight.run_preflight(root, runner=runner)
            self.assertFalse(result["ready"])
            self.assertEqual("KB_CONFIG_MISSING", result["notices"][-1]["code"])
            runner.assert_called_once()

    def test_cli_is_silent_when_healthy_and_compact_on_attention(self):
        healthy = {
            "schema_version": "byteworker-session-preflight/v1",
            "status": "healthy",
            "ready": True,
            "kb": "/tmp/kb",
            "runtime": {"large": "detail"},
            "notices": [],
        }
        output = io.StringIO()
        with (
            mock.patch.object(PREFLIGHT_CLI, "run_preflight", return_value=healthy),
            redirect_stdout(output),
        ):
            self.assertEqual(0, PREFLIGHT_CLI.main([]))
        self.assertEqual("", output.getvalue())

        attention = {
            **healthy,
            "status": "attention",
            "notices": [{"code": "TODO_REMINDERS", "message": "one"}],
        }
        output = io.StringIO()
        with (
            mock.patch.object(PREFLIGHT_CLI, "run_preflight", return_value=attention),
            redirect_stdout(output),
        ):
            self.assertEqual(0, PREFLIGHT_CLI.main([]))
        payload = json.loads(output.getvalue())
        self.assertNotIn("runtime", payload)
        self.assertEqual("TODO_REMINDERS", payload["notices"][0]["code"])


if __name__ == "__main__":
    unittest.main()
