import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTES = ROOT / "references/workflow-routes.json"
REFERENCE_RE = re.compile(r"`?(references/[A-Za-z0-9._/-]+(?:\.md|\.json))`?")


class AgentRouteContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads(ROUTES.read_text(encoding="utf-8"))
        cls.workflows = cls.manifest["workflows"]

    def closure(self, name, stack=()):
        self.assertNotIn(name, stack, f"workflow extends cycle: {stack + (name,)}")
        value = self.workflows[name]
        result = []
        parent = value.get("extends")
        if parent:
            self.assertIn(parent, self.workflows)
            result.extend(self.closure(parent, stack + (name,)))
        result.extend(value.get("required", []))
        return list(dict.fromkeys(result))

    def test_every_route_file_exists_and_reference_budgets_hold(self):
        budgets = self.manifest["budgets"]
        for name, workflow in self.workflows.items():
            with self.subTest(workflow=name):
                paths = self.closure(name)
                for conditional in (
                    workflow.get("source_type", {}),
                    workflow.get("features", {}),
                ):
                    for values in conditional.values():
                        paths.extend(values)
                paths.extend(workflow.get("on_error", []))
                for relative in paths:
                    self.assertTrue((ROOT / relative).is_file(), relative)
                if name in budgets:
                    characters = sum(
                        len((ROOT / relative).read_text(encoding="utf-8"))
                        for relative in self.closure(name)
                    )
                    self.assertLessEqual(
                        characters,
                        budgets[name],
                        f"{name} reference closure={characters}",
                    )

    def test_independent_digest_entrypoints_include_full_safety_closure(self):
        required = {
            "references/machine-protocol.md",
            "references/digest-core.md",
            "references/digest-dependencies.md",
            "references/digest-transaction.md",
            "references/provenance.md",
            "references/write-rules.md",
            "references/conflict-policy.md",
            "references/semantic-policy.md",
        }
        for name in ("digest", "large_digest_worker", "wiki_resume_page"):
            with self.subTest(workflow=name):
                self.assertTrue(required.issubset(self.closure(name)))

    def test_unattended_prompts_reference_machine_checked_route(self):
        for relative in (
            "templates/report-automation-daily.md",
            "templates/report-automation-weekly.md",
            "templates/report-automation-recovery.md",
            "templates/dreaming-runner.md",
            "references/digest-large.md",
            "references/wiki-digest-jobs.md",
        ):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("references/workflow-routes.json", text)

    def test_router_and_shared_protocol_stay_compact(self):
        limits = {
            "SKILL.md": 11_000,
            "references/machine-protocol.md": 5_000,
            "references/commands.md": 1_500,
        }
        for relative, limit in limits.items():
            with self.subTest(path=relative):
                self.assertLessEqual(
                    len((ROOT / relative).read_text(encoding="utf-8")),
                    limit,
                )

    def test_removed_inbox_is_not_an_agent_workflow(self):
        self.assertNotIn("inbox", self.workflows)
        self.assertNotIn("inbox", self.manifest["budgets"])

    def test_dreaming_routes_enable_tour_and_maintenance_on_demand(self):
        features = self.workflows["dreaming"]["features"]
        self.assertEqual(
            ["references/dreaming-setup-guide.md"],
            features["configure"],
        )
        self.assertEqual(
            [
                "references/dreaming-onboarding.md",
                "references/dreaming-setup-guide.md",
            ],
            features["enable"],
        )
        self.assertEqual(
            ["references/dreaming-maintenance.md"],
            features["maintenance"],
        )
        self.assertEqual(
            ["references/dreaming-harness-trae.md"],
            features["harness_trae"],
        )
        onboarding = (ROOT / features["enable"][0]).read_text(encoding="utf-8")
        setup_guide = (ROOT / features["configure"][0]).read_text(
            encoding="utf-8"
        )
        trae_harness = (ROOT / features["harness_trae"][0]).read_text(
            encoding="utf-8"
        )
        maintenance = (ROOT / features["maintenance"][0]).read_text(
            encoding="utf-8"
        )
        for term in (
            "它与 digest 的区别",
            "能做什么",
            "默认值与授权",
            "成本、隐私与运行条件",
            "--acknowledge-capability-tour",
            "--acknowledge-schedule",
            "每几天固定时间",
            "内部复验仍检查",
        ):
            self.assertIn(term, onboarding)
        for term in (
            "后台信息助手",
            "结构化提问或选项控件",
            "从第一个未完成",
            "只想修改一项",
            "每天给我汇总重要信息",
            "为什么没有自动运行",
            "会包含私聊和免打扰会话",
            "不得暗中开启",
            "不得因命令覆盖语义意外关闭",
            "自动运行：已接通",
            "自动运行：待完成",
        ):
            self.assertIn(term, setup_guide)
        for internal, user_term in (
            ("`operational`", "自动运行是否接通"),
            ("`persist_report`", "生成定时摘要"),
            ("`instant_alert`", "紧急事项及时提醒"),
            ("harness", "本地定时任务"),
            ("Finding", "待关注事项"),
        ):
            with self.subTest(internal=internal):
                self.assertIn(internal, setup_guide)
                self.assertIn(user_term, setup_guide)
        for term in (
            "doctor scan",
            "doctor fix",
            "DOCTOR_USER_DECISION_REQUIRED",
            "waiting_for_user",
            "不得猜业务语义",
        ):
            self.assertIn(term, maintenance)
        for term in (
            "byteworker-dreaming-local",
            "本地任务唤醒间隔",
            "推荐 2 小时",
            "Code 模式",
            "本地环境",
            "Run now",
            "自动运行：待完成",
            "不得向用户输出",
            "harness register",
            "禁止猜内部",
            "不得把名称中的 `TRAE` 当成支持定时任务的充分条件",
            "TRAE IDE/TraeCode",
            "提示用户切换到 TraeWork 桌面版",
            "TraeWork 网页版仅提供云端运行环境",
            "即使当前会话暴露 Schedule 工具",
            "不创建任务，也不执行",
        ):
            self.assertIn(term, trae_harness)
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
        for text in (skill, architecture):
            self.assertIn("TRAE IDE/TraeCode", text)
            self.assertIn("TraeWork 桌面版", text)

    def test_core_policies_have_no_known_contradictory_fallbacks(self):
        paths = [
            ROOT / "SKILL.md",
            ROOT / "DESIGN.md",
            *(ROOT / "references").glob("*.md"),
            *(ROOT / "templates").glob("*.md"),
        ]
        markdown = "\n".join(path.read_text(encoding="utf-8") for path in paths)
        self.assertNotIn("以更晚的来源为准", markdown)
        self.assertNotIn("实体类倾向 `area`、记录类倾向 `event`", markdown)
        self.assertNotIn("实体类倾向 `node-area`", markdown)
        self.assertNotIn("证据不足写「证据有限」", markdown)


if __name__ == "__main__":
    unittest.main()
