import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from source_profiles import (  # noqa: E402
    SourceProfileError,
    list_profiles,
    load_profile,
    profile_relative_path,
    save_profile,
    validate_profile,
)


def profile(
    *,
    app_id=101,
    dashboard_id=202,
    sheet_id=303,
    report_ids=None,
    where=None,
    routine="weekly",
):
    report_ids = list(report_ids or [])
    where = list(where or [])
    return {
        "schema_version": "byteworker-source-profile/v1",
        "source_type": "aeolus",
        "source_uid": f"aeolus:cn:{app_id}:{dashboard_id}:{sheet_id}",
        "source_url": (
            f"https://data.bytedance.net/aeolus/pages/dashboard/{dashboard_id}"
            f"?appId={app_id}&sheetId={sheet_id}"
        ),
        "title": f"Dashboard {dashboard_id} / Sheet {sheet_id}",
        "coordinates": {
            "region": "cn",
            "app_id": app_id,
            "dashboard_id": dashboard_id,
            "sheet_id": sheet_id,
        },
        "capture": {
            "report_selector": {
                "mode": "include" if report_ids else "all",
                "report_ids": report_ids,
            },
            "filters": {
                "mode": "explicit" if where else "dashboard",
                "where": where,
            },
            "max_items_per_report": 1000,
        },
        "routine": {
            "enabled": bool(routine),
            "cadence": routine or None,
        },
    }


class SourceProfileTests(unittest.TestCase):
    def test_two_dashboard_sheets_keep_independent_selectors_and_filters(self):
        first = validate_profile(
            profile(
                report_ids=[401],
                where=[
                    {
                        "name": "区域",
                        "dimMetId": 601,
                        "op": "in",
                        "val": ["CN"],
                    }
                ],
            )
        )
        second = validate_profile(
            profile(
                dashboard_id=212,
                sheet_id=313,
                report_ids=[402, 403],
                where=[
                    {
                        "name": "月份",
                        "dimMetId": 602,
                        "op": "in",
                        "val": ["2026-07"],
                    }
                ],
                routine="daily",
            )
        )
        self.assertNotEqual(profile_relative_path(first), profile_relative_path(second))
        self.assertEqual(
            [401],
            first["capture"]["report_selector"]["report_ids"],
        )
        self.assertEqual(
            [402, 403],
            second["capture"]["report_selector"]["report_ids"],
        )
        self.assertEqual(
            ["CN"],
            first["capture"]["filters"]["where"][0]["val"],
        )
        self.assertEqual(
            ["2026-07"],
            second["capture"]["filters"]["where"][0]["val"],
        )
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            for value in (first, second):
                path = kb / profile_relative_path(value)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(value, ensure_ascii=False),
                    encoding="utf-8",
                )
            loaded = {
                value["source_uid"]: value
                for value in list_profiles(kb, source_type="aeolus")
            }
            self.assertEqual({first["source_uid"], second["source_uid"]}, set(loaded))
            self.assertEqual(
                [401],
                loaded[first["source_uid"]]["capture"]["report_selector"]["report_ids"],
            )
            self.assertEqual(
                ["2026-07"],
                loaded[second["source_uid"]]["capture"]["filters"]["where"][0]["val"],
            )

    def test_profile_rejects_credentials_anywhere(self):
        value = profile()
        value["capture"]["token"] = "secret"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(value)
        self.assertEqual(
            "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )
        value = profile()
        value["source_url"] += "&bytecloud_jwt=secret"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(value)
        self.assertEqual(
            "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )

    def test_save_profile_commits_only_kb_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            (kb / "knowledge").mkdir()
            (kb / "raw_data").mkdir()
            subprocess.run(["git", "init", "-q"], cwd=kb, check=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.test"],
                cwd=kb,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=kb,
                check=True,
            )
            (kb / "INDEX.md").write_text("# initial\n", encoding="utf-8")
            subprocess.run(["git", "add", "INDEX.md"], cwd=kb, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "init"],
                cwd=kb,
                check=True,
            )
            value = profile(report_ids=[401])
            receipt = save_profile(
                kb,
                value,
                skill_root=ROOT,
                now=datetime(2026, 7, 29, 10, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertEqual("committed", receipt["status"])
            self.assertEqual(value["source_uid"], load_profile(kb, value["source_uid"])["source_uid"])
            self.assertEqual(1, len(list_profiles(kb)))
            changed = subprocess.run(
                ["git", "show", "--pretty=format:", "--name-only", "HEAD"],
                cwd=kb,
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            ).stdout.splitlines()
            self.assertEqual(
                {
                    receipt["profile_path"],
                    "INDEX.md",
                    "journal/2026-07/2026-07-29.md",
                },
                set(changed),
            )
            saved = json.loads((kb / receipt["profile_path"]).read_text())
            self.assertNotIn("token", json.dumps(saved))


if __name__ == "__main__":
    unittest.main()
