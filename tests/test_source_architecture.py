import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
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
        self.assertIn("source_operation_types()", source)
        self.assertIn("run_source_operation(args", source)
        self.assertNotIn('args.source_type == "', source)

    def test_final_source_contract_is_persisted_in_root_architecture(self):
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        design = (ROOT / "DESIGN.md").read_text(encoding="utf-8")
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
        self.assertIn("`ARCHITECTURE.md` §4.3、§8.3", design)


if __name__ == "__main__":
    unittest.main()
