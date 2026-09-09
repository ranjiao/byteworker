import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
LAUNCHER_PATH = ROOT / "bin" / "byteworker"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from command_registry import (  # noqa: E402
    command_aliases,
    command_description,
    command_manifest,
    command_specs,
    facade_entrypoints,
    preferred_path_for,
    resolve_command_alias,
    required_sources_for,
    validate_registry,
)


def load_launcher():
    path = ROOT / "bin" / "byteworker-launcher.py"
    spec = importlib.util.spec_from_file_location("command_registry_launcher_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


LAUNCHER = load_launcher()


class CommandRegistryTests(unittest.TestCase):
    def run_launcher(self, *args, env=None):
        return subprocess.run(
            [str(LAUNCHER_PATH), *args],
            cwd=ROOT,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_registry_is_valid_and_paths_exist(self):
        self.assertEqual([], validate_registry())
        for spec in command_specs(include_hidden=True):
            with self.subTest(command=spec.name):
                self.assertTrue((ROOT / spec.docs).is_file(), spec.docs)
                if spec.entrypoint:
                    self.assertTrue((ROOT / "bin" / spec.entrypoint).is_file())

    def test_facade_mapping_is_derived_from_registry(self):
        expected = {
            spec.name: spec.entrypoint
            for spec in command_specs(include_hidden=True)
            if spec.execution == "facade"
        }
        self.assertEqual(expected, facade_entrypoints())

    def test_manifest_hides_tombstone_by_default(self):
        visible = command_manifest()
        all_commands = command_manifest(include_hidden=True)
        self.assertNotIn("inbox", {item["name"] for item in visible["commands"]})
        self.assertIn("inbox", {item["name"] for item in all_commands["commands"]})
        self.assertEqual(len(visible["commands"]), visible["count"])
        self.assertIn("digest", visible["namespaces"])
        self.assertIn("kb", visible["namespaces"])
        self.assertEqual(
            "byteworker-cli-artifact/v1",
            visible["output_policy"]["artifact_protocol"],
        )
        self.assertEqual(
            1024 * 1024,
            visible["output_policy"]["inline_stdout_limit_bytes"],
        )
        self.assertEqual(
            {alias.to_dict()["path"] for alias in command_aliases()},
            {item["path"] for item in visible["aliases"]},
        )

    def test_aliases_resolve_and_describe_preferred_paths(self):
        self.assertEqual(
            ["digest-flow", "start", "--kb", "/tmp/kb"],
            resolve_command_alias(
                ["digest", "flow", "start", "--kb", "/tmp/kb"]
            ),
        )
        self.assertEqual(
            ["digest-run", "show", "run-1"],
            resolve_command_alias(["digest", "inspect", "run", "show", "run-1"]),
        )
        self.assertEqual(
            ("kb", "query", "search"),
            preferred_path_for(("kb-query", "search")),
        )
        described = command_description("digest.flow.start")
        self.assertIsNotNone(described)
        command = described["command"]
        self.assertEqual("digest.flow.start", command["command_path"])
        self.assertEqual("digest-flow.start", command["alias_target"])
        self.assertEqual(
            "bin/byteworker digest flow start --help", command["help_command"]
        )

    def test_runtime_requirements_are_registry_driven_and_help_is_free(self):
        self.assertEqual(
            {"feishu"},
            required_sources_for(
                ["source", "capture", "--source-type", "feishu_chat"]
            ),
        )
        self.assertEqual(
            {"meego"},
            required_sources_for(["source", "inspect", "--source-type", "meego"]),
        )
        self.assertEqual({"feishu"}, required_sources_for(["wiki", "scan"]))
        self.assertEqual(
            set(),
            required_sources_for(
                ["source", "capture", "--source-type", "feishu_chat", "--help"]
            ),
        )
        self.assertEqual(set(), required_sources_for(["wiki", "inspect", "--help"]))

    def test_top_help_contains_native_and_facade_commands(self):
        result = self.run_launcher("--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        for name in ("preflight", "deps", "runtime-reset", "commands"):
            self.assertIn(name, result.stdout)
        for name in facade_entrypoints():
            if name != "inbox":
                self.assertIn(name, result.stdout)
        self.assertNotIn("  inbox ", result.stdout)

    def test_native_and_tombstone_help_is_read_only(self):
        for name in ("deps", "run", "runtime-reset", "inbox", "lark", "meegle"):
            with self.subTest(command=name):
                result = self.run_launcher(name, "--help")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertTrue(result.stdout.startswith("usage:"), result.stdout)
                self.assertEqual("", result.stderr)

        with mock.patch.object(LAUNCHER, "clear_runtime_cache") as clear:
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, LAUNCHER.main(["runtime-reset", "--help"]))
        clear.assert_not_called()

    def test_nested_help_bypasses_runtime_and_envelope(self):
        with (
            mock.patch.object(LAUNCHER, "_exec", return_value=0) as execute,
            mock.patch.object(LAUNCHER, "cached_check_runtime") as runtime,
        ):
            self.assertEqual(0, LAUNCHER.main(["wiki", "inspect", "--help"]))
        runtime.assert_not_called()
        argv, _env = execute.call_args.args
        self.assertTrue(argv[1].endswith("bin/wiki.py"))
        self.assertEqual(["inspect", "--help"], argv[2:])

        result = self.run_launcher("source", "bundle", "--help")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(result.stdout.startswith("usage:"), result.stdout)
        self.assertNotIn('"protocol":"byteworker-cli/v1"', result.stdout)

    def test_namespace_and_alias_help_bypass_runtime(self):
        with (
            mock.patch.object(LAUNCHER, "_exec", return_value=0) as execute,
            mock.patch.object(LAUNCHER, "cached_check_runtime") as runtime,
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, LAUNCHER.main(["digest", "--help"]))
        execute.assert_not_called()
        runtime.assert_not_called()
        self.assertIn("usage: byteworker digest <command>", output.getvalue())
        self.assertIn("flow", output.getvalue())
        self.assertIn("inspect", output.getvalue())

        with (
            mock.patch.object(LAUNCHER, "_exec", return_value=0) as execute,
            mock.patch.object(LAUNCHER, "cached_check_runtime") as runtime,
        ):
            self.assertEqual(
                0, LAUNCHER.main(["digest", "flow", "start", "--help"])
            )
        runtime.assert_not_called()
        argv, _env = execute.call_args.args
        self.assertTrue(argv[1].endswith("bin/digest-flow.py"))
        self.assertEqual(["start", "--help"], argv[2:])

    def test_alias_execution_uses_compatible_facade_path(self):
        with (
            mock.patch.object(LAUNCHER, "_exec", return_value=0) as execute,
            mock.patch.object(
                LAUNCHER,
                "cached_check_runtime",
                return_value=({"ready": True}, "hit"),
            ),
            mock.patch.object(LAUNCHER, "runtime_environment", return_value={}),
        ):
            self.assertEqual(
                0, LAUNCHER.main(["kb", "query", "search", "release"])
            )
        argv, _env = execute.call_args.args
        self.assertTrue(argv[1].endswith("bin/byteworker-cli.py"))
        self.assertEqual(["kb-query", "search", "release"], argv[2:])

    def test_discovery_commands_are_machine_readable(self):
        listed = self.run_launcher("commands", "list", "--json")
        self.assertEqual(0, listed.returncode, listed.stderr)
        manifest = json.loads(listed.stdout)
        self.assertEqual("byteworker-command-manifest/v1", manifest["schema_version"])

        described = self.run_launcher(
            "commands", "describe", "source.capture", "--json"
        )
        self.assertEqual(0, described.returncode, described.stderr)
        value = json.loads(described.stdout)
        self.assertEqual("source.capture", value["command"]["command_path"])
        self.assertEqual("mixed", value["command"]["side_effect"])

    def test_facade_context_contains_registry_metadata(self):
        result = self.run_launcher("source", "capabilities")
        self.assertEqual(0, result.returncode, result.stderr)
        context = json.loads(result.stdout)["context"]
        self.assertEqual("source.capabilities", context["command_path"])
        self.assertEqual("stable", context["stability"])
        self.assertEqual("mixed", context["side_effect"])

    def test_documented_python_version_matches_launcher(self):
        for path in (ROOT / "README.md", ROOT / "INSTALL.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("python3 >= 3.10", text)
                self.assertNotIn("python3 >= 3.9", text)

    def test_help_does_not_create_python_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_bin = ROOT / "bin"
            target_bin = root / "bin"
            target_lib = root / "lib"
            target_bin.mkdir()
            target_lib.mkdir()
            for name in ("byteworker", "byteworker-launcher.py"):
                (target_bin / name).write_bytes((source_bin / name).read_bytes())
            (target_bin / "byteworker").chmod(0o755)
            for name in (
                "command_registry.py",
                "machine_protocol.py",
                "runtime_deps.py",
            ):
                (target_lib / name).write_bytes((LIB / name).read_bytes())
            env = {
                **os.environ,
                "HOME": str(root),
                "PATH": "/usr/bin:/bin:/usr/local/bin",
            }
            completed = subprocess.run(
                [str(target_bin / "byteworker"), "--help"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertFalse((root / ".python-cache.txt").exists())
            self.assertFalse((root / ".runtime-cache.json").exists())


if __name__ == "__main__":
    unittest.main()
