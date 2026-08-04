from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AreaScopeContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_skill_requires_explicit_area_scope(self):
        skill = self.read("SKILL.md")
        self.assertIn("标题和概述必须显式写出业务、团队或个人限定语", skill)
        self.assertIn("不得合并成看似公司级的通用方法论", skill)

    def test_digest_and_update_share_area_scope_boundary(self):
        write_rules = self.read("references/write-rules.md")
        update = self.read("references/command-update.md")
        self.assertIn("area 主题领域的业务 / 团队边界", write_rules)
        self.assertIn("推进节奏、指标口径、决策权、成熟度和技术共识", write_rules)
        self.assertIn("相似方案不能作为合并", write_rules)
        self.assertIn("标题、H1、TL;DR 与概述首句都写限定语", update)
        self.assertIn("无法确认归属时暂停 area mutation", update)

    def test_area_template_prompts_for_scope_in_visible_fields(self):
        template = self.read("templates/node-area.md")
        scoped_title = "<业务/团队/个人限定语>：<主题领域名>"
        self.assertEqual(2, template.count(scoped_title))
        self.assertIn("明确不代表其它业务/团队或公司级共识", template)
        self.assertIn("首句重申业务/团队/个人适用范围", template)


if __name__ == "__main__":
    unittest.main()
