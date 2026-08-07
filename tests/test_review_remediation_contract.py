from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReviewRemediationContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_report_jobs_have_one_success_completion_path(self):
        runner = self.read("templates/dreaming-runner.md")
        self.assertIn("报告成功路径不得调用", runner)
        self.assertIn("报告成功路径唯一", runner)
        self.assertIn("禁止再调用通用 `dreaming complete`", runner)

    def test_dashboard_refresh_does_not_create_business_facts(self):
        dashboard = self.read("references/command-dashboard.md")
        design = self.read("docs/development/DESIGN.md")
        architecture = self.read("docs/development/ARCHITECTURE.md")
        for text in (dashboard, design, architecture):
            self.assertIn("不得", text)
            self.assertIn("业务事实", text)
        self.assertIn("只从当天既有 journal 渲染", dashboard)

    def test_unknown_authors_are_not_attributed_to_kb_owner(self):
        paths = (
            "references/digest-analysis.md",
            "templates/node-area.md",
            "templates/node-project.md",
        )
        for relative_path in paths:
            with self.subTest(path=relative_path):
                text = self.read(relative_path)
                self.assertIn("作者未知", text)
                self.assertIn("不得默认归给知识库主人", text)

    def test_daily_and_weekly_templates_have_todo_sections(self):
        for relative_path in (
            "templates/report-daily.md",
            "templates/report-weekly.md",
        ):
            with self.subTest(path=relative_path):
                template = self.read(relative_path)
                self.assertIn("## 你的 Todo", template)
                self.assertIn("不伪造 [S]", template)


if __name__ == "__main__":
    unittest.main()
