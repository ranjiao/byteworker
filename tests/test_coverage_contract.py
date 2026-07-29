import configparser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CoverageContractTests(unittest.TestCase):
    def test_branch_and_subprocess_coverage_have_a_minimum_gate(self) -> None:
        config = configparser.ConfigParser()
        config.read(ROOT / ".coveragerc", encoding="utf-8")

        self.assertTrue(config.getboolean("run", "branch"))
        self.assertIn("bin", config.get("run", "source").split())
        self.assertIn("lib", config.get("run", "source").split())
        self.assertIn("subprocess", config.get("run", "patch").split())
        self.assertGreaterEqual(config.getfloat("report", "fail_under"), 75.0)

    def test_ci_runs_all_required_validation_layers(self) -> None:
        workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")

        for required in (
            "setup-node",
            "jq",
            "compileall",
            'bash -n "$script"',
            "coverage run -m unittest discover",
            "coverage combine",
            "coverage report",
        ):
            with self.subTest(required=required):
                self.assertIn(required, workflow)

    def test_delivery_docs_keep_the_coverage_gate_visible(self) -> None:
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

        self.assertIn("branch coverage", architecture)
        self.assertIn("coverage report", architecture)
        self.assertIn("覆盖率门禁", agents)
