import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_launcher():
    path = ROOT / "bin" / "byteworker-launcher.py"
    spec = importlib.util.spec_from_file_location("byteworker_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = load_launcher()


class ByteworkerLauncherTests(unittest.TestCase):
    def test_source_requirements_are_inferred_without_probing_every_provider(self):
        self.assertEqual(
            {"feishu"},
            LAUNCHER._required_sources(
                ["source", "capture", "--source-type", "feishu_chat"]
            ),
        )
        self.assertEqual(
            {"meego"},
            LAUNCHER._required_sources(
                ["source", "inspect", "--source-type", "meego"]
            ),
        )
        self.assertEqual({"feishu"}, LAUNCHER._required_sources(["wiki", "scan"]))
        self.assertEqual(
            set(),
            LAUNCHER._required_sources(
                ["source", "bundle-spec", "--source-type", "feishu_minutes"]
            ),
        )
        self.assertEqual(set(), LAUNCHER._required_sources(["wiki", "topics"]))
        self.assertEqual(set(), LAUNCHER._required_sources(["kb-query", "search"]))

    def test_preflight_does_not_duplicate_runtime_probe(self):
        with (
            mock.patch.object(LAUNCHER, "_exec", return_value=0) as execute,
            mock.patch.object(LAUNCHER, "check_runtime") as check,
        ):
            self.assertEqual(0, LAUNCHER.main(["preflight", "--skip-update"]))
        check.assert_not_called()
        argv, _env = execute.call_args.args
        self.assertTrue(argv[1].endswith("bin/session-preflight.py"))

    def test_source_command_fails_closed_when_provider_runtime_is_missing(self):
        unavailable = {
            "ready": False,
            "core_ready": True,
            "python": {"status": "ok", "version": "3.11", "path": sys.executable},
            "programs": {
                "lark-cli": {
                    "required": True,
                    "status": "missing",
                    "tier": 2,
                    "version": "",
                    "error": "missing",
                }
            },
        }
        with (
            mock.patch.object(LAUNCHER, "check_runtime", return_value=unavailable),
            mock.patch.object(LAUNCHER, "_exec") as execute,
            mock.patch.object(
                LAUNCHER,
                "render_dependency_report",
                return_value="missing runtime",
            ),
        ):
            self.assertEqual(
                1,
                LAUNCHER.main(
                    ["source", "capture", "--source-type", "feishu_chat"]
                ),
            )
        execute.assert_not_called()

    def test_run_fails_closed_for_invalid_explicit_optional_runtime(self):
        unavailable = {
            "ready": False,
            "core_ready": True,
            "python": {"status": "ok", "version": "3.11", "path": sys.executable},
            "programs": {},
        }
        with (
            mock.patch.object(LAUNCHER, "check_runtime", return_value=unavailable),
            mock.patch.object(LAUNCHER, "_exec") as execute,
            mock.patch.object(
                LAUNCHER,
                "render_dependency_report",
                return_value="invalid override",
            ),
        ):
            self.assertEqual(1, LAUNCHER.main(["run", "true"]))
        execute.assert_not_called()

    def test_invalid_explicit_python_fails_before_loading_python_entrypoint(self):
        env = dict(os.environ)
        env["BYTEWORKER_PYTHON_BIN"] = "/definitely/missing/byteworker-python"
        completed = subprocess.run(
            [str(ROOT / "bin" / "byteworker"), "preflight", "--skip-update"],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(1, completed.returncode)
        self.assertEqual("", completed.stdout)
        self.assertIn("BYTEWORKER_PYTHON_BIN", completed.stderr)


if __name__ == "__main__":
    unittest.main()
