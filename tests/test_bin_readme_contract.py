from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"


class BinReadmeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = (BIN / "README.md").read_text(encoding="utf-8")

    def test_every_bin_command_is_documented(self):
        commands = sorted(
            path.name
            for path in BIN.iterdir()
            if path.is_file() and path.name != "README.md"
        )
        self.assertGreater(len(commands), 0)
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(f"`{command}`", self.readme)

    def test_readme_explains_entrypoints_and_write_boundaries(self):
        for text in (
            "## 1. 先选择正确的入口",
            "byteworker-cli/v1",
            "## 2. 公共约定与安全边界",
            "## 3. 命令总览",
            "系统临时目录或知识库目录",
            "禁止配置 remote，禁止 push",
            "只有返回 `data.status=committed`",
            "## 15. 修改或新增命令时",
        ):
            with self.subTest(text=text):
                self.assertIn(text, self.readme)

    def test_root_readme_links_command_manual(self):
        root_readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("[`bin/README.md`](bin/README.md)", root_readme)


if __name__ == "__main__":
    unittest.main()
