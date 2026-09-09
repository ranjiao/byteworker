import json
import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = ROOT / "docs/development"
CATALOG_PATH = DOCS_ROOT / "catalog.json"
MARKDOWN_LINK_RE = re.compile(r"\[[^]]+\]\(([^)]+)\)")


class DocumentationCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
        cls.documents = cls.catalog["documents"]

    def test_catalog_schema_and_entries_are_unique(self):
        self.assertEqual(
            "byteworker-document-catalog/v1", self.catalog["schema_version"]
        )
        ids = [document["id"] for document in self.documents]
        paths = [document["path"] for document in self.documents]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(paths), len(set(paths)))

        required_fields = {"id", "path", "kind", "status", "authority"}
        for document in self.documents:
            with self.subTest(document=document["id"]):
                self.assertEqual(required_fields, set(document))
                self.assertTrue((ROOT / document["path"]).is_file())

    def test_every_development_markdown_file_is_cataloged(self):
        actual = {
            path.relative_to(ROOT).as_posix()
            for path in DOCS_ROOT.rglob("*.md")
        }
        cataloged = {document["path"] for document in self.documents}
        self.assertEqual(actual, cataloged)

    def test_top_level_contains_only_stable_entrypoints(self):
        top_level_markdown = {
            path.name for path in DOCS_ROOT.glob("*.md") if path.is_file()
        }
        self.assertEqual(
            {"README.md", "ARCHITECTURE.md", "DESIGN.md"}, top_level_markdown
        )

    def test_lifecycle_matches_directory(self):
        for document in self.documents:
            path = document["path"]
            status = document["status"]
            with self.subTest(path=path):
                if "/archive/initiatives/" in path:
                    self.assertEqual("completed", status)
                elif "/archive/designs/" in path:
                    self.assertEqual("historical", status)
                elif "/evidence/benchmarks/" in path or "/evidence/reviews/" in path:
                    self.assertEqual("point_in_time", status)
                elif path.endswith("/plans/backlog.md"):
                    self.assertEqual("active", status)
                else:
                    self.assertEqual("current", status)

    def test_relative_markdown_links_resolve(self):
        for markdown_path in DOCS_ROOT.rglob("*.md"):
            text = markdown_path.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK_RE.findall(text):
                target = raw_target.strip().strip("<>")
                if target.startswith(("#", "http://", "https://", "mailto:")):
                    continue
                relative_path = target.split("#", 1)[0]
                if not relative_path:
                    continue
                resolved = (markdown_path.parent / relative_path).resolve()
                with self.subTest(source=markdown_path, target=target):
                    self.assertTrue(resolved.exists(), f"broken link: {target}")

    def test_markdown_code_fences_are_balanced(self):
        for markdown_path in DOCS_ROOT.rglob("*.md"):
            fence_count = sum(
                line.startswith("```")
                for line in markdown_path.read_text(encoding="utf-8").splitlines()
            )
            with self.subTest(path=markdown_path):
                self.assertEqual(0, fence_count % 2)

    def test_runtime_guidance_does_not_depend_on_archive(self):
        runtime_paths = [
            ROOT / "SKILL.md",
            *sorted((ROOT / "references").rglob("*.md")),
        ]
        runtime_text = "\n".join(
            path.read_text(encoding="utf-8") for path in runtime_paths
        )
        for document in self.documents:
            if "/archive/" not in document["path"]:
                continue
            with self.subTest(path=document["path"]):
                self.assertNotIn(document["path"], runtime_text)


if __name__ == "__main__":
    unittest.main()
