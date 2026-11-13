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

    def test_every_route_file_exists(self):
        self.assertEqual(
            "byteworker-workflow-routes/v2",
            self.manifest["schema_version"],
        )
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
                for relative in workflow.get("worker_prompt", []):
                    self.assertTrue((ROOT / relative).is_file(), relative)

    def test_declared_core_sources_have_machine_checked_routes(self):
        source_routes = self.workflows["digest"]["source_type"]
        self.assertIn("feishu_minutes", source_routes)
        self.assertIn("references/digest-meeting.md", source_routes["feishu_minutes"])
        self.assertIn(
            "references/digest-comments.md",
            source_routes["feishu_doc"],
        )
        self.assertIn("wiki_space", self.workflows)
        self.assertEqual(
            {
                "references/machine-protocol.md",
                "references/digest-wiki-space.md",
            },
            set(self.closure("wiki_space")),
        )

    def test_independent_digest_entrypoints_include_full_safety_closure(self):
        required = {
            "references/machine-protocol.md",
            "references/digest-core.md",
            "references/digest-dependencies.md",
            "references/digest-inline.md",
            "references/provenance.md",
            "references/write-rules.md",
            "references/conflict-policy.md",
            "references/semantic-policy.md",
        }
        for name in ("digest", "large_digest_worker", "wiki_resume_page"):
            with self.subTest(workflow=name):
                self.assertTrue(required.issubset(self.closure(name)))
                if name != "digest":
                    self.assertIn("references/digest-transaction.md", self.closure(name))

    def test_ordinary_digest_defers_manuals_but_preserves_all_safety_policies(self):
        route = self.workflows["digest"]
        deferred = {
            "references/digest-observability.md": "recovery",
            "references/digest-analysis-pipeline.md": "parallel_execution",
            "references/digest-concurrency.md": "parallel_execution",
            "references/digest-transaction.md": "batch",
        }
        for path, feature in deferred.items():
            with self.subTest(path=path):
                self.assertNotIn(path, route["required"])
                self.assertIn(path, route["features"][feature])
        self.assertIn("references/digest-inline.md", route["required"])
        self.assertIn("references/digest-comments.md", route["source_type"]["feishu_doc"])
        self.assertIn("references/digest-reading.md", route["features"]["internal_reading"])
        self.assertNotIn("references/digest-inline.md", self.workflows["dreaming"]["features"]["routine_digest"])

    def test_digest_feedback_has_a_separate_on_demand_route(self):
        reference = "references/digest-feedback.md"
        self.assertIn(reference, self.closure("digest_feedback"))
        self.assertNotIn(reference, self.closure("doctor"))
        self.assertNotIn(reference, self.closure("digest"))


    def test_failed_payload_recovery_keeps_transaction_rules_and_stops_writing(self):
        paths = self.closure("digest") + self.workflows["digest"]["features"]["recovery"]
        self.assertIn("references/digest-transaction.md", paths)
        self.assertIn("references/digest-observability.md", paths)

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


    def test_moved_mutation_policies_remain_in_required_closures(self):
        for workflow in ("digest", "update", "large_digest_worker", "wiki_resume_page", "digest_final_reducer"):
            with self.subTest(workflow=workflow):
                self.assertIn("references/write-rules.md", self.closure(workflow))

    def test_removed_inbox_is_not_an_agent_workflow(self):
        self.assertNotIn("inbox", self.workflows)
        self.assertNotIn("inbox", self.manifest["budgets"])

    def test_removed_todo_is_not_an_agent_workflow(self):
        self.assertNotIn("todo", self.workflows)
        self.assertNotIn("todo", self.manifest["budgets"])

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
        self.assertEqual(["references/dreaming-routine.md"], features["routine_digest"])
        self.assertEqual(["references/dreaming-harness-trae.md"], features["harness_trae"])


if __name__ == "__main__":
    unittest.main()
