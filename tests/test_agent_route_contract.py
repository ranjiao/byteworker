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
