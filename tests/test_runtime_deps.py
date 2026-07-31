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

from runtime_deps import (  # noqa: E402
    CACHE_SCHEMA_VERSION,
    PYTHON_CACHE_FILENAME,
    RUNTIME_CACHE_FILENAME,
    cached_check_runtime,
    check_runtime,
    clear_runtime_cache,
    read_runtime_cache,
    runtime_environment,
    write_runtime_cache,
)


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
                python_version=(3, 9, 6),
                runner=self.successful_probe,
            )
            self.assertFalse(result["core_ready"])
            self.assertEqual("missing", result["python"]["status"])


class RuntimeCacheTests(unittest.TestCase):
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

    @staticmethod
    def fake_python_probe(argv, **kwargs):
        if argv[1:3] == ["-c", "import sys,zoneinfo;raise SystemExit(0 if sys.version_info>=(3,10) else 1)"]:
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        name = Path(argv[0]).name
        return subprocess.CompletedProcess(
            argv, 0, stdout=f"{name} test-version\n", stderr=""
        )

    def layout(self, root: Path) -> tuple[Path, Path]:
        home = root / "home"
        tools = home / "tools"
        for name in ("git", "jq", "bash", "node", "lark-cli"):
            self.executable(tools / name)
        return home, tools

    def test_write_and_read_runtime_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                required_sources={"feishu"},
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            ok, status = write_runtime_cache(root, result, environ=env, home=home)
            self.assertTrue(ok, msg=status)
            self.assertTrue((root / RUNTIME_CACHE_FILENAME).is_file())
            cached, reason = read_runtime_cache(root, environ=env, home=home)
            self.assertIsNotNone(cached, msg=reason)
            self.assertEqual(CACHE_SCHEMA_VERSION, cached.get("schema_version"))
            self.assertEqual(
                result["python"]["path"], cached.get("python", {}).get("path")
            )

    def test_python_cache_is_written_by_bash_layer_not_python_layer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            write_runtime_cache(root, result, environ=env, home=home)
            self.assertFalse(
                (root / PYTHON_CACHE_FILENAME).is_file(),
                ".python-cache.txt 应由 Bash 层写入,不应由 Python 层写入",
            )

    def test_read_cache_missing_returns_reason(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cached, reason = read_runtime_cache(root)
            self.assertIsNone(cached)
            self.assertIn("missing", reason)

    def test_path_change_does_not_invalidate_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            write_runtime_cache(root, result, environ=env, home=home)
            alt_env = {"HOME": str(home), "PATH": str(tools) + ":/different"}
            cached, reason = read_runtime_cache(root, environ=alt_env, home=home)
            self.assertIsNotNone(cached, msg=reason)

    def test_old_cache_does_not_expire(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            write_runtime_cache(root, result, environ=env, home=home)
            cache_path = root / RUNTIME_CACHE_FILENAME
            import json as _json
            data = _json.loads(cache_path.read_text(encoding="utf-8"))
            data["generated_at"] = 1
            cache_path.write_text(
                _json.dumps(data, separators=(",", ":")), encoding="utf-8"
            )
            cached, reason = read_runtime_cache(root, environ=env, home=home)
            self.assertIsNotNone(cached, msg=reason)

    def test_cached_check_runtime_hit_skips_fresh_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            seed_result, seed_status = cached_check_runtime(
                root,
                required_sources={"feishu"},
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            self.assertIn("cached", seed_status)
            probe_calls = {"count": 0}

            def counting_probe(argv, **kwargs):
                probe_calls["count"] += 1
                return self.fake_python_probe(argv, **kwargs)

            hit_result, status = cached_check_runtime(
                root,
                required_sources={"feishu"},
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=counting_probe,
            )
            self.assertEqual("hit", status)
            self.assertTrue(hit_result["ready"])
            self.assertEqual(
                seed_result["programs"]["git"]["path"],
                hit_result["programs"]["git"]["path"],
            )

    def test_cached_readiness_is_recomputed_for_current_requirements(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}

            def failing_lark_probe(argv, **kwargs):
                if Path(argv[0]).name == "lark-cli":
                    return subprocess.CompletedProcess(
                        argv, 1, stdout="", stderr="broken"
                    )
                return self.successful_probe(argv, **kwargs)

            seed = check_runtime(
                required_sources={"feishu"},
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=failing_lark_probe,
            )
            self.assertFalse(seed["ready"])
            write_runtime_cache(root, seed, environ=env, home=home)

            core_result, core_status = cached_check_runtime(
                root,
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.fake_python_probe,
            )
            self.assertEqual("hit", core_status)
            self.assertTrue(core_result["ready"])

            feishu_result, feishu_status = cached_check_runtime(
                root,
                required_sources={"feishu"},
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=failing_lark_probe,
            )
            self.assertEqual("miss+cached", feishu_status)
            self.assertFalse(feishu_result["ready"])

    def test_cached_check_runtime_force_refresh_bypasses_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            cached_check_runtime(
                root,
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            _result, status = cached_check_runtime(
                root,
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
                force_refresh=True,
            )
            self.assertIn("cached", status)

    def test_clear_runtime_cache_removes_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            write_runtime_cache(root, result, environ=env, home=home)
            self.assertTrue((root / RUNTIME_CACHE_FILENAME).is_file())
            removed, message = clear_runtime_cache(root)
            self.assertTrue(removed)
            self.assertIn("removed", message)
            self.assertFalse((root / RUNTIME_CACHE_FILENAME).is_file())
            self.assertFalse((root / PYTHON_CACHE_FILENAME).is_file())
            removed_again, _ = clear_runtime_cache(root)
            self.assertFalse(removed_again)

    def test_explicit_byteworker_python_bin_invalidates_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            home, tools = self.layout(root)
            env = {"HOME": str(home), "PATH": str(tools)}
            result = check_runtime(
                environ=env,
                home=home,
                python_executable=sys.executable,
                python_version=(3, 11, 0),
                runner=self.successful_probe,
            )
            write_runtime_cache(root, result, environ=env, home=home)
            env_override = dict(env)
            env_override["BYTEWORKER_PYTHON_BIN"] = "/some/other/python"
            cached, reason = read_runtime_cache(
                root, environ=env_override, home=home
            )
            self.assertIsNone(cached)
            self.assertIn("BYTEWORKER_PYTHON_BIN", reason)


if __name__ == "__main__":
    unittest.main()
