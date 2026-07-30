import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WikiColdPathTests(unittest.TestCase):
    def test_facade_uses_subprocess_mapping_without_importing_wiki_modules(self):
        source = (ROOT / "bin" / "byteworker-cli.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("wiki_explorer", imported)
        self.assertNotIn("digest_jobs", imported)
        self.assertIn('"wiki": "wiki.py"', source)
        self.assertIn('"digest-job": "digest-job.py"', source)

    def test_skill_only_routes_to_lazy_wiki_references(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("references/digest-wiki-space.md", skill)
        self.assertNotIn("WIKI_KEYCHAIN_ACCESS_BLOCKED", skill)
        self.assertNotIn("byteworker-wiki-tree-state/v1", skill)
        self.assertLessEqual(skill.count("feishu_wiki"), 1)
        self.assertTrue((ROOT / "references" / "wiki-digest-jobs.md").is_file())

    def test_normal_core_does_not_name_wiki_state_or_job_schema(self):
        for relative_path in ("lib/digest_txn.py", "lib/kb_query.py"):
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            self.assertNotIn("byteworker-wiki-tree-state", text)
            self.assertNotIn("byteworker-digest-job", text)
            self.assertNotIn("state/wiki", text)


if __name__ == "__main__":
    unittest.main()
