import ast
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import source_operations
from source_operation_contract import argument
PROVIDER_NAMES = {
    "feishu_doc",
    "feishu_minutes",
    "feishu_meeting",
    "feishu_chat",
    "feishu_base",
    "feishu_wiki",
    "meego",
    "aeolus",
    "web",
}


class SourceArchitectureTests(unittest.TestCase):
    def _string_literals(self, relative_path: str) -> set[str]:
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source)
        return {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }

    def test_transaction_and_query_core_do_not_name_providers(self):
        for relative_path in ("lib/digest_txn.py", "lib/kb_query.py"):
            with self.subTest(path=relative_path):
                literals = self._string_literals(relative_path)
                self.assertEqual(set(), literals & PROVIDER_NAMES)

    def test_source_cli_dispatches_through_operation_registry(self):
        source = (ROOT / "bin/source.py").read_text(encoding="utf-8")
        self.assertIn("source_operation_arguments(name)", source)
        self.assertIn("persist_result(args, run(args", source)
        self.assertNotIn('args.source_type == "', source)
        self.assertNotIn('add_argument("--project-key"', source)
        self.assertNotIn('add_argument("--report-id"', source)

    def test_test_provider_extends_parser_and_runtime_without_cli_edit(self):
        class TestProvider:
            source_type = "test_provider"
            operation_arguments = {
                "inspect": (argument("--cursor", default=""),),
            }
            runtime_requirements = {"inspect": ("feishu",)}

            def run(self, args, *, skill_root):
                return {"cursor": args.cursor}

        before = (ROOT / "bin/source.py").read_bytes()
        with mock.patch.dict(
            source_operations._ADAPTERS,
            {"test_provider": TestProvider()},
        ):
            self.assertIn(
                "test_provider",
                source_operations.operation_source_types("inspect"),
            )
            self.assertIn(
                "cursor",
                {
                    spec.destination
                    for spec in source_operations.source_operation_arguments("inspect")
                },
            )
            self.assertEqual(
                ("feishu",),
                source_operations.source_operation_runtime("test_provider", "inspect"),
            )
        self.assertEqual(before, (ROOT / "bin/source.py").read_bytes())

    def test_final_source_contract_is_persisted_in_root_architecture(self):
        architecture = (ROOT / "docs/development/ARCHITECTURE.md").read_text(encoding="utf-8")
        design = (ROOT / "docs/development/DESIGN.md").read_text(encoding="utf-8")
        self.assertFalse(
            (ROOT / "references/source-architecture-refactor.md").exists()
        )
        for contract in (
            "`byteworker-source-bundle/v2`",
            "`digest-plan/v2`",
            "`digest-batch-plan/v2`",
            "`byteworker-record-index/v1`",
            "`lib/sources/transaction_bridge.py`",
            "`lib/sources/record_projection.py`",
        ):
            with self.subTest(contract=contract):
                self.assertIn(contract, architecture)
        self.assertIn("`docs/development/ARCHITECTURE.md` §4.3、§8.3", design)


if __name__ == "__main__":
    unittest.main()
