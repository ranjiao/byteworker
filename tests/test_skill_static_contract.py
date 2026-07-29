import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")


class SkillStaticContractTests(unittest.TestCase):
    def test_all_local_markdown_links_resolve(self):
        missing = []
        markdown_files = [
            path
            for path in ROOT.rglob("*.md")
            if not any(
                part == ".git" or part == ".gstack" or part.startswith(".tmp")
                for part in path.parts
            )
        ]
        for source in markdown_files:
            text = source.read_text(encoding="utf-8")
            for raw_target in MARKDOWN_LINK_RE.findall(text):
                target = raw_target.strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1]
                target = target.split(maxsplit=1)[0]
                if (
                    not target
                    or target.startswith(("#", "http://", "https://", "mailto:", "app://"))
                ):
                    continue
                path_text = target.split("#", 1)[0]
                if not path_text:
                    continue
                resolved = (source.parent / path_text).resolve()
                try:
                    resolved.relative_to(ROOT)
                except ValueError:
                    continue
                if not resolved.exists():
                    missing.append(
                        f"{source.relative_to(ROOT)} -> {target}"
                    )
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main()
