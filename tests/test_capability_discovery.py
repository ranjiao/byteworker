import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from capability_discovery import (  # noqa: E402
    CATALOG_SCHEMA,
    STATE_SCHEMA,
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


if __name__ == "__main__":
    unittest.main()
