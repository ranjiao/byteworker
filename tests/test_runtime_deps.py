from pathlib import Path
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
import sys

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from runtime_deps import check_runtime, runtime_environment  # noqa: E402


class RuntimeDependencyTests(unittest.TestCase):
    def executable(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
        return path

    @staticmethod
    def successful_probe(argv, **kwargs):
        name = Path(argv[0]).name
        return subprocess.CompletedProcess(
            argv,
            0,
            stdout=f"{name} test-version\n",
            stderr="",
        )

    def test_discovers_latest_nvm_runtime_when_path_is_incomplete(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tools = home / "tools"
            for name in ("git", "jq", "bash"):
                self.executable(tools / name)
            old = home / ".nvm/versions/node/v20.1.0/bin"
            latest = home / ".nvm/versions/node/v22.19.0/bin"
            self.executable(old / "node")
            self.executable(old / "lark-cli")
            self.executable(latest / "node")
            self.executable(latest / "lark-cli")
            result = check_runtime(
                required_sources={"feishu"},
                environ={"HOME": str(home), "PATH": str(tools)},
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            self.assertTrue(result["ready"])
            self.assertIn("v22.19.0", result["programs"]["node"]["path"])
            self.assertIn("v22.19.0", result["programs"]["lark-cli"]["path"])
            env = runtime_environment(result, environ={"PATH": str(tools)})
            self.assertEqual(
                result["programs"]["lark-cli"]["path"],
                env["BYTEWORKER_LARK_CLI_BIN"],
            )
            self.assertIn(str(latest), env["PATH"].split(os.pathsep))
            self.assertNotIn(str(old), env["PATH"].split(os.pathsep))

    def test_provider_wrapper_directory_precedes_separately_resolved_node(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tools = home / "tools"
            for name in ("git", "jq", "bash"):
                self.executable(tools / name)
            old = home / ".nvm/versions/node/v20.1.0/bin"
            latest = home / ".nvm/versions/node/v22.19.0/bin"
            self.executable(old / "node")
            self.executable(old / "lark-cli")
            self.executable(latest / "node")
            result = check_runtime(
                required_sources={"feishu"},
                environ={"HOME": str(home), "PATH": str(tools)},
                home=home,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            self.assertIn(str(latest), result["programs"]["node"]["path"])
            self.assertIn(str(old), result["programs"]["lark-cli"]["path"])
            env = runtime_environment(result, environ={"PATH": str(tools)})
            self.assertLess(
                env["PATH"].split(os.pathsep).index(str(old)),
                env["PATH"].split(os.pathsep).index(str(latest)),
            )

    def test_invalid_explicit_lark_path_does_not_silently_fallback(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tools = home / "tools"
            for name in ("git", "jq", "bash", "node", "lark-cli"):
                self.executable(tools / name)
            missing = home / "missing-lark-cli"
            result = check_runtime(
                required_sources={"feishu"},
                environ={
                    "HOME": str(home),
                    "PATH": str(tools),
                    "BYTEWORKER_LARK_CLI_BIN": str(missing),
                },
                home=home,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            self.assertFalse(result["ready"])
            self.assertEqual("", result["programs"]["lark-cli"]["path"])
            self.assertIn(
                "BYTEWORKER_LARK_CLI_BIN",
                result["programs"]["lark-cli"]["error"],
            )

    def test_explicit_optional_runtime_is_required_without_source_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tools = home / "tools"
            for name in ("git", "jq", "bash"):
                self.executable(tools / name)
            result = check_runtime(
                environ={
                    "HOME": str(home),
                    "PATH": str(tools),
                    "BYTEWORKER_LARK_CLI_BIN": str(home / "missing"),
                },
                home=home,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            self.assertFalse(result["ready"])
            self.assertTrue(result["programs"]["lark-cli"]["required"])

    def test_broken_runtime_is_not_injected_into_command_environment(self):
        result = {
            "python": {"path": sys.executable},
            "programs": {
                "lark-cli": {
                    "path": "/tmp/broken/lark-cli",
                    "status": "broken",
                }
            },
        }
        env = runtime_environment(
            result,
            environ={"PATH": "/usr/bin", "BYTEWORKER_LARK_CLI_BIN": "stale"},
        )
        self.assertEqual("stale", env["BYTEWORKER_LARK_CLI_BIN"])
        self.assertNotIn("/tmp/broken", env["PATH"].split(os.pathsep))

    def test_python_version_is_a_core_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            tools = home / "tools"
            for name in ("git", "jq", "bash"):
                self.executable(tools / name)
            result = check_runtime(
                environ={"HOME": str(home), "PATH": str(tools)},
                home=home,
                python_version=(3, 8, 18),
                runner=self.successful_probe,
            )
            self.assertFalse(result["core_ready"])
            self.assertEqual("missing", result["python"]["status"])


if __name__ == "__main__":
    unittest.main()
