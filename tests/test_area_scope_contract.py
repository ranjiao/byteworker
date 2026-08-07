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

    def test_digest_requires_disambiguated_knowledge_titles(self):
        skill = self.read("SKILL.md")
        digest_core = self.read("references/digest-core.md")
        write_rules = self.read("references/write-rules.md")
        design = self.read("docs/development/DESIGN.md")
        architecture = self.read("docs/development/ARCHITECTURE.md")

        self.assertIn("raw / Bundle 的", skill)
        self.assertIn("按来源可确认的作者、团队", skill)
        self.assertIn("库内标题消歧", digest_core)
        self.assertIn("不得为了消歧改写 raw 正文或 `source_title`", digest_core)
        self.assertIn("raw / SourceBundle 的 `source_title` 保留来源原题", write_rules)
        self.assertIn("无法确认范围时，不得臆造团队或项目归属", write_rules)
        self.assertIn("来源标题与库内标题分离", design)
        self.assertIn("为库内节点生成可消歧标题", architecture)

    def test_reading_template_prompts_for_disambiguated_title(self):
        template = self.read("templates/node-reading.md")
        scoped_title = "<库内消歧标题:必要时包含作者/团队/业务/项目/周期限定语>"
        self.assertEqual(2, template.count(scoped_title))
        self.assertIn("这篇文章 / 资料在什么作者/团队/业务/项目/周期范围内成立", template)
        self.assertIn("原始标题", template)

    def test_reading_reference_handles_ambiguous_internal_titles(self):
        reading = self.read("references/digest-reading.md")
        self.assertIn("内部资料标题消歧", reading)
        self.assertIn("`个人工作总结` → `<作者>：<周期>个人工作总结`", reading)
        self.assertIn("原始标题继续保存在 raw 的 `source_title`", reading)


if __name__ == "__main__":
    unittest.main()
