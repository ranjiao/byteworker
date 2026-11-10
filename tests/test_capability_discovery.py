import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from capability_discovery import (  # noqa: E402
    CATALOG_SCHEMA,
    STATE_SCHEMA,
    complete,
    diagnose,
    feedback,
    load_catalog,
    peek_background_recommendation,
    recommend,
    state_path,
    status,
)


NOW = datetime(2026, 9, 9, 8, 0, tzinfo=timezone.utc)


class CapabilityDiscoveryTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        for relative in (
            ".git/info",
            "knowledge/projects",
            "knowledge/people",
            "sources",
            "reports/daily",
            "reports/weekly",
        ):
            (kb / relative).mkdir(parents=True, exist_ok=True)
        (kb / ".git/info/exclude").write_text("", encoding="utf-8")
        return kb

    def test_catalog_is_user_facing_and_never_recommends_dreaming(self):
        catalog = load_catalog()
        self.assertEqual(CATALOG_SCHEMA, catalog["schema_version"])
        ids = {item["id"] for item in catalog["capabilities"]}
        self.assertIn("digest", ids)
        self.assertIn("thinking", ids)
        dreaming = next(
            item for item in catalog["capabilities"] if item["id"] == "dreaming"
        )
        self.assertEqual("explicit_only", dreaming["proactive_policy"])
        self.assertNotIn(
            "dreaming",
            {item["capability_id"] for item in catalog["recommendations"]},
        )

    def test_first_digest_recommends_search_and_writes_private_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            result = recommend(kb, after="digest", now=NOW)
            self.assertEqual("digest", result["recorded_success"])
            self.assertEqual("search", result["suggestion"]["capability_id"])

            saved = json.loads(state_path(kb).read_text(encoding="utf-8"))
            self.assertEqual(STATE_SCHEMA, saved["schema_version"])
            self.assertEqual(1, saved["events"]["digest"])
            self.assertEqual(1, saved["capabilities"]["search"]["impressions"])
            self.assertEqual(0o600, state_path(kb).stat().st_mode & 0o777)
            self.assertIn(
                "/state/", (kb / ".git/info/exclude").read_text(encoding="utf-8")
            )

    def test_cooldown_then_repeated_source_tip(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            recommend(kb, after="digest", now=NOW)
            blocked = recommend(
                kb,
                after="digest",
                signals=["repeated_source"],
                now=NOW + timedelta(days=1),
            )
            self.assertIsNone(blocked["suggestion"])

            due = recommend(
                kb,
                after="digest",
                signals=["repeated_source"],
                now=NOW + timedelta(days=8),
            )
            self.assertEqual("routine", due["suggestion"]["capability_id"])

    def test_dismiss_and_global_disable_suppress_tips(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            feedback(kb, action="dismiss", capability_id="search", now=NOW)
            result = recommend(kb, after="digest", now=NOW)
            self.assertIsNone(result["suggestion"])

            feedback(kb, action="disable", now=NOW)
            overview = status(kb, now=NOW)
            self.assertFalse(overview["tips_enabled"])
            self.assertEqual(
                "dismissed",
                next(
                    item
                    for item in overview["capabilities"]
                    if item["id"] == "search"
                )["progress"],
            )

    def test_background_tip_is_peeked_until_shown(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            for index in range(3):
                (kb / "knowledge/projects" / f"project-{index}.md").write_text(
                    "# project\n", encoding="utf-8"
                )
            first = peek_background_recommendation(kb, now=NOW)
            second = peek_background_recommendation(kb, now=NOW)
            self.assertEqual("dashboard", first["capability_id"])
            self.assertEqual(first, second)

            feedback(
                kb,
                action="shown",
                capability_id="dashboard",
                now=NOW,
            )
            self.assertIsNone(peek_background_recommendation(kb, now=NOW))

    def test_second_manual_report_can_recommend_automation(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            first = recommend(kb, after="reports", now=NOW)
            self.assertIsNone(first["suggestion"])
            second = recommend(
                kb,
                after="reports",
                now=NOW + timedelta(days=8),
            )
            self.assertEqual("reports", second["suggestion"]["capability_id"])

    def test_direct_cli_and_facade_return_machine_readable_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            direct = subprocess.run(
                [sys.executable, str(ROOT / "bin/discover.py"), "status", "--kb", str(kb)],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, direct.returncode, direct.stderr)
            self.assertEqual(
                "byteworker-capability-status/v1",
                json.loads(direct.stdout)["schema_version"],
            )

            facade = subprocess.run(
                [str(ROOT / "bin/byteworker"), "discover", "status", "--kb", str(kb)],
                cwd=ROOT,
                env={**os.environ, "BYTEWORKER_NO_AUTO_UPDATE": "1"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, facade.returncode, facade.stderr)
            envelope = json.loads(facade.stdout)
            self.assertEqual("success", envelope["status"])
            self.assertEqual(
                "byteworker-capability-status/v1",
                envelope["data"]["schema_version"],
            )

    def test_interactive_completion_reaches_milestones_without_preflight_and_no_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            for index in range(3):
                (kb / "knowledge/projects" / f"project-{index}.md").touch()
            first = complete(kb, after="search", interactive=True, now=NOW)
            self.assertEqual("dashboard", first["suggestion"]["capability_id"])
            second = complete(kb, after="search", interactive=True, now=NOW)
            self.assertIsNone(second["suggestion"])
            saved = json.loads(state_path(kb).read_text())
            self.assertEqual(2, saved["events"]["search"])
            self.assertEqual(1, saved["capabilities"]["dashboard"]["impressions"])
            # Using the candidate capability makes it ineligible even after cooldown.
            self.assertIsNone(complete(kb, after="dashboard", interactive=True,
                                     now=NOW + timedelta(days=8))["suggestion"])

    def test_failed_and_unattended_completions_do_not_create_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            for interactive, outcome, reason in (
                (False, "success", "unattended"),
                (True, "failed", "task_failed"),
                (True, "needs_input", "task_needs_input"),
            ):
                result = complete(kb, after="digest", interactive=interactive,
                                  outcome=outcome, now=NOW)
                self.assertEqual(reason, result["reason"])
                self.assertIsNone(result["suggestion"])
                self.assertFalse((kb / "state").exists())

    def test_contextual_priority_and_update_judgment_signal(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            for index in range(3):
                (kb / "knowledge/projects" / f"project-{index}.md").touch()
            first = complete(kb, after="digest", interactive=True, now=NOW)
            self.assertEqual("search", first["suggestion"]["capability_id"])
            result = complete(kb, after="update", interactive=True,
                              signals=["user_judgment"], now=NOW + timedelta(days=8))
            self.assertEqual("thinking", result["suggestion"]["capability_id"])

    def test_diagnosis_is_read_only_and_explains_suppression(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            result = diagnose(kb, after="digest", interactive=True, now=NOW)
            self.assertFalse(result["state_exists"])
            self.assertFalse((kb / "state").exists())
            rules = {r["id"]: r for r in result["rules"]}
            self.assertIn("insufficient_events", rules["search-after-first-digest"]["reasons"])
            self.assertIn("signal_required", rules["routine-after-repeated-source"]["reasons"])
            self.assertIn("insufficient_facts", rules["dashboard-for-multiple-projects"]["reasons"])
            feedback(kb, action="snooze", capability_id="routine", now=NOW)
            feedback(kb, action="dismiss", capability_id="thinking", now=NOW)
            complete(kb, after="digest", interactive=True, now=NOW)
            before = state_path(kb).read_bytes()
            result = diagnose(kb, after="digest", interactive=True, now=NOW)
            self.assertEqual(before, state_path(kb).read_bytes())
            rules = {r["id"]: r for r in result["rules"]}
            self.assertIn("snoozed", rules["routine-after-repeated-source"]["reasons"])
            self.assertIn("dismissed", rules["thinking-after-user-judgment"]["reasons"])
            self.assertIn("impression_limit", rules["search-after-first-digest"]["reasons"])
            self.assertIn("cooldown", rules["dashboard-for-multiple-projects"]["reasons"])
            feedback(kb, action="disable", now=NOW)
            self.assertIn("tips_disabled", diagnose(kb, interactive=True)["gates"])
            self.assertIn("unattended", diagnose(kb)["gates"])

    def test_cli_completion_requires_interactive_and_diagnose_does_not_consume(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            def run(*args):
                result = subprocess.run(
                    [str(ROOT / "bin/byteworker"), "discover", *args, "--kb", str(kb)],
                    env={**os.environ, "BYTEWORKER_NO_AUTO_UPDATE": "1"},
                    capture_output=True, text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
                return json.loads(result.stdout)["data"]
            for operation in ("complete", "recommend"):
                for mode in ([], ["--unattended"]):
                    result = run(operation, "--after", "digest", *mode)
                    self.assertEqual("unattended", result["reason"])
                    self.assertFalse(state_path(kb).exists())
            run("diagnose", "--interactive", "--after", "digest")
            self.assertFalse(state_path(kb).exists())
            self.assertEqual("search", run("complete", "--interactive", "--after", "digest")
                             ["suggestion"]["capability_id"])

    def test_every_interactive_business_route_loads_completion_contract(self):
        manifest = json.loads((ROOT / "references/workflow-routes.json").read_text())
        for name in ("digest", "search", "update", "brief", "dashboard",
                     "context", "thinking", "report"):
            self.assertIn("references/discovery-completion.md",
                          manifest["workflows"][name]["required"], name)

    def test_daily_interaction_with_old_report_settings_reaches_one_tip(self):
        import session_preflight
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            (kb / "context.md").touch()
            for index in range(3):
                (kb / "knowledge/projects" / f"project-{index}.md").touch()
            (kb / "state").mkdir()
            (kb / "state/report_automation.json").write_text(json.dumps({
                "schema_version": "byteworker-report-automation/v1",
                "decision": "configured", "prompt_version": 2,
            }))
            with (
                mock.patch.object(session_preflight, "cached_check_runtime", return_value=(
                    {"ready": True, "core_ready": True,
                     "python": {"path": sys.executable}, "programs": {}}, "hit")),
            ):
                result = session_preflight.run_preflight(
                    ROOT, kb_override=kb, skip_update=True, allow_capability_suggestions=True
                )
                self.assertEqual(
                    ["REPORT_AUTOMATION_PROMPT_UPGRADE", "CAPABILITY_SUGGESTION"],
                    [notice["code"] for notice in result["notices"]],
                )
                self.assertFalse(state_path(kb).exists())
                done = complete(kb, after="search", interactive=True, now=NOW)
                self.assertEqual("dashboard", done["suggestion"]["capability_id"])
                before = state_path(kb).read_bytes()
                automatic = session_preflight.run_preflight(
                    ROOT, kb_override=kb, skip_update=True
                )
                self.assertNotIn("CAPABILITY_SUGGESTION",
                                 [notice["code"] for notice in automatic["notices"]])
                self.assertEqual("unattended", complete(kb, after="reports")["reason"])
                self.assertEqual(before, state_path(kb).read_bytes())


if __name__ == "__main__":
    unittest.main()
