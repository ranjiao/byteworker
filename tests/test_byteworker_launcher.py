import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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
            mock.patch.object(LAUNCHER, "cached_check_runtime") as check,
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
            mock.patch.object(
                LAUNCHER, "cached_check_runtime", return_value=(unavailable, "miss")
            ),
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
            mock.patch.object(
                LAUNCHER, "cached_check_runtime", return_value=(unavailable, "miss")
            ),
            mock.patch.object(LAUNCHER, "_exec") as execute,
            mock.patch.object(
                LAUNCHER,
                "render_dependency_report",
                return_value="invalid override",
            ),
        ):
            self.assertEqual(1, LAUNCHER.main(["run", "true"]))
        execute.assert_not_called()

    def test_runtime_reset_command_clears_cache(self):
        with mock.patch.object(
            LAUNCHER,
            "clear_runtime_cache", return_value=(True, "removed runtime-cache.json")
        ) as clear:
            self.assertEqual(0, LAUNCHER.main(["runtime-reset"]))
        clear.assert_called_once()

    def test_deps_command_refresh_flag(self):
        available = {
            "ready": True,
            "core_ready": True,
            "python": {"status": "ok", "version": "3.11", "path": sys.executable, "tier": 1, "required": True, "error": ""},
            "programs": {
                "git": {"status": "ok", "tier": 1, "required": True, "path": "/usr/bin/git", "version": "", "error": ""},
                "jq": {"status": "ok", "tier": 1, "required": True, "path": "/usr/bin/jq", "version": "", "error": ""},
                "bash": {"status": "ok", "tier": 1, "required": True, "path": "/bin/bash", "version": "", "error": ""},
                "node": {"status": "ok", "tier": 2, "required": False, "path": "/usr/bin/node", "version": "", "error": ""},
                "lark-cli": {"status": "ok", "tier": 2, "required": False, "path": "/usr/bin/lark-cli", "version": "", "error": ""},
                "meegle": {"status": "ok", "tier": 2, "required": False, "path": "/usr/bin/meegle", "version": "", "error": ""},
            },
        }
        with mock.patch.object(
            LAUNCHER,
            "cached_check_runtime", return_value=(available, "miss+cached")
        ) as check:
            self.assertEqual(0, LAUNCHER.main(["deps", "--refresh"]))
        check.assert_called_once()
        _args, kwargs = check.call_args
        self.assertTrue(kwargs.get("force_refresh"))

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

    def test_python_cache_has_no_ttl(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            launcher = bin_dir / "byteworker"
            shutil.copy2(ROOT / "bin" / "byteworker", launcher)
            (bin_dir / "byteworker-launcher.py").write_text("", encoding="utf-8")
            marker = root / "selected-python.txt"
            cached_python = root / "cached-python"
            cached_python.write_text(
                "#!/bin/sh\n"
                "if [ \"$1\" = \"-c\" ]; then\n"
                "  case \"$2\" in *print*) echo 3.12.7 ;; esac\n"
                "  exit 0\n"
                "fi\n"
                "printf '%s\\n' \"$0\" > \"$BYTEWORKER_TEST_MARKER\"\n",
                encoding="utf-8",
            )
            cached_python.chmod(0o755)
            (root / ".python-cache.txt").write_text(
                f"{cached_python}\n3.12.7\n0\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(root),
                "PATH": "/usr/bin:/bin",
                "BYTEWORKER_TEST_MARKER": str(marker),
            }
            completed = subprocess.run(
                [str(launcher), "deps", "--cache-status"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, completed.returncode, msg=completed.stderr)
            self.assertEqual(str(cached_python), marker.read_text().strip())

    def test_deps_refresh_replaces_python_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            tools = root / "tools"
            bin_dir.mkdir()
            tools.mkdir()
            launcher = bin_dir / "byteworker"
            shutil.copy2(ROOT / "bin" / "byteworker", launcher)
            (bin_dir / "byteworker-launcher.py").write_text("", encoding="utf-8")
            marker = root / "selected-python.txt"

            def fake_python(path: Path, label: str) -> None:
                path.write_text(
                    "#!/bin/sh\n"
                    "if [ \"$1\" = \"-c\" ]; then\n"
                    "  case \"$2\" in *print*) echo 3.12.7 ;; esac\n"
                    "  exit 0\n"
                    "fi\n"
                    f"printf '%s\\n' '{label}' > \"$BYTEWORKER_TEST_MARKER\"\n",
                    encoding="utf-8",
                )
                path.chmod(0o755)

            cached_python = root / "cached-python"
            refreshed_python = tools / "python3"
            fake_python(cached_python, "cached")
            fake_python(refreshed_python, "refreshed")
            (root / ".python-cache.txt").write_text(
                f"{cached_python}\n3.12.7\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HOME": str(root),
                "PATH": f"{tools}:/usr/bin:/bin",
                "BYTEWORKER_TEST_MARKER": str(marker),
            }
            completed = subprocess.run(
                [str(launcher), "deps", "--refresh"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, completed.returncode, msg=completed.stderr)
            self.assertEqual("refreshed", marker.read_text().strip())
            self.assertEqual(
                str(refreshed_python),
                (root / ".python-cache.txt").read_text().splitlines()[0],
            )


if __name__ == "__main__":
    unittest.main()
