"""Enforce dependency boundaries without freezing architecture prose."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def imports(relative):
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            result.add(node.module.split(".")[0])
    return result


class ArchitectureContractTests(unittest.TestCase):
    def test_existing_workflows_do_not_depend_on_dreaming_scheduler(self):
        for module in ("digest_txn", "kb_query", "session_preflight", "report_automation"):
            with self.subTest(module=module):
                self.assertNotIn("dreaming_scheduler", imports(f"lib/{module}.py"))

    def test_dreaming_domain_uses_facades_for_knowledge_writes(self):
        modules = (
            "dreaming_state", "dreaming_models", "dreaming_grants", "dreaming_collection",
            "dreaming_batch", "dreaming_analysis", "dreaming_consolidation", "dreaming_process",
            "dreaming_digest", "dreaming_action_policy", "dreaming_action_ledger", "dreaming_reports",
            "report_owner", "dreaming_evaluation", "dreaming_scheduler",
        )
        for module in modules:
            with self.subTest(module=module):
                self.assertFalse(imports(f"lib/{module}.py") & {"digest_txn", "kb_query"})
        self.assertNotIn("report_automation", imports("lib/dreaming_scheduler.py"))


if __name__ == "__main__":
    unittest.main()
