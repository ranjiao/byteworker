from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CitationContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_skill_routes_all_kb_answers_to_citation_protocol(self) -> None:
        skill = self.read("SKILL.md")
        self.assertIn("知识库检索回答引用(每次必做)", skill)
        self.assertIn("references/citations.md", skill)
        self.assertIn("原始出处、收录时间与置信度", skill)

    def test_protocol_requires_original_source_and_canonical_ingested_time(self) -> None:
        protocol = self.read("references/citations.md")
        self.assertIn("结论 → 原始来源", protocol)
        self.assertIn("收录时间**:`ingested`", protocol)
        self.assertIn("不得从文件名、节点创建时间或 git 时间猜测", protocol)
        self.assertIn("关键结论的原始出处或收录时间缺失", protocol)

    def test_retrieval_commands_apply_citations_to_search_brief_dashboard(self) -> None:
        commands = self.read("references/commands.md")
        self.assertGreaterEqual(commands.count("references/citations.md"), 3)
        self.assertIn("溯源解析(必做)", commands)
        self.assertIn("每个知识库事实段落 / 列表项就近标 `[S<n>]`", commands)

    def test_report_templates_keep_claim_level_citations(self) -> None:
        for relative_path in (
            "templates/report-daily.md",
            "templates/report-weekly.md",
            "templates/report-im.md",
        ):
            with self.subTest(template=relative_path):
                template = self.read(relative_path)
                self.assertIn("[S1]", template)
                self.assertIn("## 引用", template)
                self.assertRegex(template, r"收录|扫描")


if __name__ == "__main__":
    unittest.main()
