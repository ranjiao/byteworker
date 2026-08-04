from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class OrgDirectoryScopeContractTests(unittest.TestCase):
    def test_skill_requires_official_department_path_and_confirmed_leader(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertIn("完整正式部门路径", skill)
        self.assertIn("resolve-users.sh --format json", skill)
        self.assertIn("person 的 `department_path`", skill)
        self.assertIn("待用户确认", skill)
        self.assertIn("不能从成员、职级", skill)
        self.assertIn("通讯录当前归属", skill)
        self.assertIn("管理职责", skill)
        self.assertIn("汇报关系", skill)
        self.assertIn("禁止创建重复人物", skill)
        self.assertIn("项目协作、会议同现和历史链接不证明", skill)

    def test_digest_and_update_keep_directory_and_leader_evidence_distinct(self):
        digest_core = (ROOT / "references/digest-core.md").read_text(
            encoding="utf-8"
        )
        update = (ROOT / "references/command-update.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("`department_path` 作为正式组织名", digest_core)
        self.assertIn("负责人确认日期与目录核验日期分开记录", digest_core)
        self.assertIn("实时飞书目录结果", update)
        self.assertIn("冲突时请用户裁决", update)
        self.assertIn("细粒度职责不能覆盖较粗 `department_path`", update)
        self.assertIn("旧关系降为带日期的", update)

    def test_write_rules_forbid_path_fragment_tree_and_leader_inference(self):
        rules = (ROOT / "references/write-rules.md").read_text(encoding="utf-8")

        self.assertIn("org 组织节点的飞书架构对齐", rules)
        self.assertIn("不自行缩写、拼接或把路径片段分别建成组织", rules)
        self.assertIn("证明谁是组织负责人", rules)
        self.assertIn("飞书目录核验日期不能冒充负责人确认日期", rules)
        self.assertIn("person/org 关系必须区分三种事实", rules)
        self.assertIn("负责人关系不自动等于直属汇报", rules)
        self.assertIn("禁止按字符串相似度新建重复 person", rules)
        self.assertIn("项目协作、会议同现、周报署名", rules)

    def test_org_template_exposes_name_and_leader_verification_prompts(self):
        template = (ROOT / "templates/node-org.md").read_text(encoding="utf-8")

        self.assertIn("飞书通讯录完整正式部门路径", template)
        self.assertIn("person.department_path + directory_verified_at", template)
        self.assertIn("负责人：<用户确认姓名", template)
        self.assertIn("与目录核验日期分开", template)
        self.assertIn("通讯录归属、管理职责、汇报关系分别记录", template)

        person_template = (ROOT / "templates/node-person.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("通讯录当前归属", person_template)
        self.assertIn("管理职责", person_template)
        self.assertIn("汇报关系", person_template)
        self.assertIn("历史协作 link 不表示当前成员归属", person_template)


if __name__ == "__main__":
    unittest.main()
