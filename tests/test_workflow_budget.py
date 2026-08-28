import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from workflow_budget import (  # noqa: E402
    ESTIMATOR_METHOD,
    TOKENIZER_METHOD,
    estimate_tokens,
    inspect_budget,
    load_manifest,
)


class WorkflowBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = load_manifest()

    def test_estimator_is_exact_or_explicitly_versioned(self):
        receipt = estimate_tokens("中文 workflow rules and evidence")
        self.assertGreater(receipt["tokens"], 0)
        self.assertIn(receipt["method"], {ESTIMATOR_METHOD, TOKENIZER_METHOD})
        self.assertEqual(receipt["method"] == TOKENIZER_METHOD, receipt["exact"])

    def test_every_complete_route_scenario_fits_declared_token_budget(self):
        for workflow, route in self.manifest["workflows"].items():
            features = list(route.get("features", {}))
            source_types = list(route.get("source_type", {})) or [""]
            for source_type in source_types:
                with self.subTest(workflow=workflow, source_type=source_type):
                    receipt = inspect_budget(
                        workflow=workflow,
                        source_type=source_type,
                        features=features,
                        include_on_error=True,
                    )
                    self.assertEqual("within_budget", receipt["status"], receipt)
                    self.assertTrue(all(receipt["checks"].values()), receipt)

    def test_digest_budget_includes_router_conditionals_and_error_policy(self):
        receipt = inspect_budget(
            workflow="digest",
            source_type="feishu_doc",
            features=["comments", "whiteboard"],
            include_on_error=True,
        )
        paths = receipt["route"]["static_rule_paths"]
        self.assertIn("SKILL.md", paths)
        self.assertIn("references/digest-doc.md", paths)
        self.assertIn("references/digest-comments.md", paths)
        self.assertIn("references/digest-whiteboard.md", paths)
        self.assertIn("references/error-handling.md", paths)

    def test_worker_budget_measures_prompt_without_loading_router(self):
        receipt = inspect_budget(workflow="digest_semantic_worker")
        self.assertNotIn("SKILL.md", receipt["route"]["static_rule_paths"])
        self.assertEqual(
            ["templates/digest-worker-prompt.md"],
            receipt["route"]["worker_prompt_paths"],
        )
        self.assertGreater(receipt["measurement"]["worker_prompt_tokens"], 0)

    def test_dynamic_overflow_returns_action_without_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "context.md"
            context.write_text("context " * 100_000, encoding="utf-8")
            receipt = inspect_budget(workflow="search", context_path=context)
        self.assertEqual("over_budget", receipt["status"])
        self.assertEqual("compact_context_or_ask_user", receipt["action"])
        self.assertFalse(receipt["checks"]["dynamic_context_tokens"])

    def test_cli_returns_budget_receipt(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "workflow-budget.py"),
                "inspect",
                "--workflow",
                "digest",
                "--source-type",
                "local_md",
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual("byteworker-workflow-budget-receipt/v1", receipt["schema_version"])
        self.assertIn("static_rules_tokens", receipt["measurement"])
        self.assertIn("total_input_tokens", receipt["budget"])
        self.assertIn("output_tokens", receipt["budget"])


if __name__ == "__main__":
    unittest.main()
