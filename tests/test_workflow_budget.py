from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from workflow_budget import (  # noqa: E402
    ESTIMATOR_METHOD,
    TOKENIZER_METHOD,
    WorkflowBudgetError,
    estimate_tokens,
    inspect_budget,
    load_manifest,
    resolve_route,
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

    def test_approved_overflow_is_bound_to_input_and_host_capacity(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = Path(temporary) / "context.md"
            context.write_text("alpha " * 100_000, encoding="utf-8")
            args = {"workflow": "search", "context_path": context}
            original = inspect_budget(**args)
            approval = original["review"]["id"]
            required = original["measurement"]["total_input_tokens"]
            self.assertIn("dynamic_context_tokens", original["review"]["exceeded_limits"])
            with self.assertRaises(WorkflowBudgetError) as limited:
                inspect_budget(**args, approved_review_id=approval, host_input_limit=required - 1)
            self.assertEqual("WORKFLOW_BUDGET_HOST_LIMIT", limited.exception.code)

            approved = inspect_budget(**args, approved_review_id=approval, host_input_limit=required)
            self.assertEqual("within_budget", approved["status"])
            self.assertTrue(approved["review"]["approved"])
            self.assertEqual("over_budget", inspect_budget(**args)["status"])

            context.write_text("bravo " * 100_000, encoding="utf-8")
            with self.assertRaises(WorkflowBudgetError) as changed:
                inspect_budget(**args, approved_review_id=approval, host_input_limit=required)
            self.assertEqual("WORKFLOW_BUDGET_APPROVAL_INVALID", changed.exception.code)

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

    def resolve_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin/workflow-budget.py"), "resolve", *args],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )

    def test_resolve_cli_matches_inspect_without_loading_rule_text_or_budgets(self):
        result = self.resolve_cli("--workflow", "digest", "--source-type", "feishu_doc")
        self.assertEqual(0, result.returncode, result.stderr)
        route = json.loads(result.stdout)
        self.assertEqual("byteworker-workflow-route/v1", route.pop("schema_version"))
        self.assertEqual(
            inspect_budget(workflow="digest", source_type="feishu_doc")["route"], route,
        )
        self.assertNotIn("budget", route)
        self.assertNotIn("measurement", route)
        self.assertLess(len(result.stdout), 1500)
        self.assertNotIn("references/workflow-routes.json", route["static_rule_paths"])

    def test_resolve_cli_selects_features_and_failure_closure(self):
        result = self.resolve_cli(
            "--workflow", "digest", "--source-type", "feishu_doc",
            "--feature", "parallel_execution", "--feature", "recovery", "--include-on-error",
        )
        self.assertEqual(0, result.returncode, result.stderr)
        paths = json.loads(result.stdout)["static_rule_paths"]
        for path in ("digest-concurrency.md", "digest-observability.md", "digest-transaction.md", "error-handling.md"):
            self.assertIn("references/" + path, paths)
        self.assertEqual(len(paths), len(set(paths)))

    def test_resolve_does_not_read_rule_bodies_or_measure_tokens(self):
        spec = importlib.util.spec_from_file_location("workflow_budget_cli_test", ROOT / "bin/workflow-budget.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["workflow-budget", "resolve", "--workflow", "digest"]),
            mock.patch.object(cli, "inspect_budget", side_effect=AssertionError("measured budget")),
            mock.patch("workflow_budget._safe_text", side_effect=AssertionError("read rule body")),
            mock.patch("workflow_budget.estimate_tokens", side_effect=AssertionError("tokenized rules")),
            redirect_stdout(output),
        ):
            self.assertEqual(0, cli.main())
        self.assertIn("references/digest-inline.md", json.loads(output.getvalue())["static_rule_paths"])

    def test_resolve_cli_rejects_invalid_route_source_and_feature(self):
        for args, code in (
            (("--workflow", "missing"), "WORKFLOW_UNKNOWN"),
            (("--workflow", "digest", "--source-type", "missing"), "WORKFLOW_SOURCE_TYPE_INVALID"),
            (("--workflow", "digest", "--feature", "missing"), "WORKFLOW_FEATURE_INVALID"),
        ):
            with self.subTest(code=code):
                result = self.resolve_cli(*args)
                self.assertEqual(1, result.returncode)
                self.assertEqual(code, json.loads(result.stdout)["error"]["code"])

    def test_normal_document_rules_have_a_fixed_size_regression_gate(self):
        route = resolve_route(self.manifest, workflow="digest", source_type="feishu_doc")
        self.assertLessEqual(len(route["static_rule_paths"]), 13)
        texts = [(ROOT / path).read_text(encoding="utf-8") for path in route["static_rule_paths"]]
        self.assertLessEqual(sum(map(len, texts)), 46_000)
        # Keep a gate independent of the optional tokenizer installed in CI.
        from workflow_budget import _conservative_tokens
        self.assertLessEqual(_conservative_tokens("\n\n".join(texts)), 48_000)


if __name__ == "__main__":
    unittest.main()
