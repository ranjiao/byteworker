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
    profile_revision,
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


def meego_profile(*, routine="daily"):
    return {
        "schema_version": "byteworker-source-profile/v2",
        "source_type": "meego",
        "source_uid": "meego:safety:view-42",
        "source_url": "https://project.feishu.cn/safety/view/view-42",
        "title": "安全需求视图",
        "selector": {
            "project_key": "safety",
            "view_id": "view-42",
        },
        "capture_policy": {
            "fields": ["updated_at", "name", "status"],
            "max_items": 500,
        },
        "routine": {
            "enabled": bool(routine),
            "cadence": routine or None,
        },
    }


def feishu_doc_profile(*, period=None, comments=True, whiteboards=True):
    capture_policy = {
        "comments": comments,
        "whiteboards": whiteboards,
    }
    if period is not None:
        capture_policy["period"] = period
    return {
        "schema_version": "byteworker-source-profile/v2",
        "source_type": "feishu_doc",
        "source_uid": "docx123",
        "source_url": "https://bytedance.larkoffice.com/docx/docx123",
        "title": "滚动周报",
        "selector": {
            "document_id": "docx123",
        },
        "capture_policy": capture_policy,
        "routine": {
            "enabled": True,
            "cadence": "weekly",
        },
    }


def feishu_base_profile():
    return {
        "schema_version": "byteworker-source-profile/v2",
        "source_type": "feishu_base",
        "source_uid": "feishu_base:bascn1:tbl1:vew1",
        "source_url": (
            "https://bytedance.larkoffice.com/base/bascn1"
            "?table=tbl1&view=vew1"
        ),
        "title": "需求 Base 视图",
        "selector": {
            "app_token": "bascn1",
            "table_id": "tbl1",
            "view_id": "vew1",
        },
        "capture_policy": {
            "fields": ["fld_updated", "fld_name", "fld_status"],
            "page_size": 200,
            "max_records": 1000,
        },
        "routine": {
            "enabled": True,
            "cadence": "weekly",
        },
    }


def feishu_chat_profile(*, since_last=True):
    capture_policy = {
        "since_last": since_last,
        "page_size": 50,
        "overlap_seconds": 30 if since_last else 0,
    }
    if not since_last:
        capture_policy.update(
            {
                "start": "2026-07-29T09:00:00+08:00",
                "end": "2026-07-29T18:00:00+08:00",
            }
        )
    return {
        "schema_version": "byteworker-source-profile/v2",
        "source_type": "feishu_chat",
        "source_uid": "oc_chat",
        "source_url": "https://applink.feishu.cn/client/chat/oc_chat",
        "title": "需求评审群",
        "selector": {
            "chat_id": "oc_chat",
        },
        "capture_policy": capture_policy,
        "routine": {
            "enabled": since_last,
            "cadence": "daily" if since_last else None,
        },
    }


def feishu_wiki_profile():
    return {
        "schema_version": "byteworker-source-profile/v2",
        "source_type": "feishu_wiki",
        "source_uid": "feishu_wiki:space-1:node-1",
        "source_url": "https://tenant.larkoffice.com/wiki/node-1",
        "title": "检索知识库子树",
        "selector": {
            "space_id": "space-1",
            "root_node_token": "node-1",
        },
        "capture_policy": {
            "max_depth": None,
            "max_nodes": 20000,
            "include_types": ["docx", "doc"],
            "change_detection": "structure_only",
        },
        "routine": {
            "enabled": True,
            "cadence": "weekly",
        },
    }


class SourceProfileTests(unittest.TestCase):
    def test_provider_validator_depends_on_neutral_contract_not_lifecycle(self):
        provider = (ROOT / "lib/source_profile_providers.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "from source_profile_contract import SourceProfileError",
            provider,
        )
        self.assertNotIn("from source_profiles import", provider)

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
        for source_url in (
            "https://user:pass@example.test/view",
            "https://example.test/view?Access-Token=secret",
            "https://example.test/view?client%5Fsecret=secret",
            "https://example.test/view#refresh_token=secret",
        ):
            with self.subTest(source_url=source_url):
                value = profile()
                value["source_url"] = source_url
                with self.assertRaises(SourceProfileError) as caught:
                    validate_profile(value)
                self.assertEqual(
                    "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
                    caught.exception.code,
                )

        # Provider resource identifiers are not authentication credentials.
        self.assertEqual(
            "bascn1",
            validate_profile(feishu_base_profile())["selector"]["app_token"],
        )
        self.assertEqual(
            "node-1",
            validate_profile(feishu_wiki_profile())["selector"][
                "root_node_token"
            ],
        )
        value = profile()
        value["source_url"] += "&bytecloud_jwt=secret"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(value)
        self.assertEqual(
            "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )

    def test_v2_meego_profile_is_strict_and_canonical(self):
        value = meego_profile()
        normalized = validate_profile(value)
        self.assertEqual("byteworker-source-profile/v2", normalized["schema_version"])
        self.assertEqual(
            ["name", "status", "updated_at"],
            normalized["capture_policy"]["fields"],
        )
        reordered = meego_profile()
        reordered["capture_policy"]["fields"] = ["status", "updated_at", "name"]
        self.assertEqual(
            profile_revision(value),
            profile_revision(reordered),
        )

        invalid = meego_profile()
        invalid["capture_policy"]["extra"] = True
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual("SOURCE_PROFILE_INVALID", caught.exception.code)

        invalid = meego_profile()
        invalid["capture_policy"]["fields"] = ["name", "name"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = meego_profile()
        invalid["source_uid"] = "meego:safety:another-view"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

    def test_v2_feishu_doc_has_optional_period_and_boolean_policies(self):
        normalized = validate_profile(feishu_doc_profile())
        self.assertEqual("", normalized["capture_policy"]["period"])
        self.assertTrue(normalized["capture_policy"]["comments"])
        self.assertTrue(normalized["capture_policy"]["whiteboards"])

        explicit = validate_profile(feishu_doc_profile(period=" 2026-W31 "))
        self.assertEqual("2026-W31", explicit["capture_policy"]["period"])

        invalid = feishu_doc_profile()
        invalid["capture_policy"]["comments"] = "all"
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_doc_profile()
        invalid["selector"]["document_id"] = "docx-other"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

    def test_v2_feishu_base_is_view_scoped_and_canonical(self):
        value = feishu_base_profile()
        normalized = validate_profile(value)
        self.assertEqual(
            ["fld_name", "fld_status", "fld_updated"],
            normalized["capture_policy"]["fields"],
        )
        self.assertEqual("bascn1", normalized["selector"]["app_token"])
        self.assertEqual(200, normalized["capture_policy"]["page_size"])

        invalid = feishu_base_profile()
        del invalid["selector"]["view_id"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_base_profile()
        invalid["source_uid"] = "feishu_base:bascn1:tbl1:another"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

        invalid = feishu_base_profile()
        invalid["capture_policy"]["page_size"] = 0
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_base_profile()
        invalid["capture_policy"]["page_size"] = 501
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_base_profile()
        invalid["selector"]["base_token"] = invalid["selector"]["app_token"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

    def test_v2_feishu_chat_normalizes_explicit_and_incremental_windows(self):
        incremental = feishu_chat_profile()
        incremental["capture_policy"]["end"] = "2026-07-29T10:00:00Z"
        normalized = validate_profile(incremental)
        self.assertEqual("", normalized["capture_policy"]["start"])
        self.assertEqual(
            "2026-07-29T10:00:00+00:00",
            normalized["capture_policy"]["end"],
        )
        self.assertEqual(30, normalized["capture_policy"]["overlap_seconds"])

        explicit = feishu_chat_profile(since_last=False)
        explicit["capture_policy"]["start"] = " 2026-07-29 09:00:00+0800 "
        normalized = validate_profile(explicit)
        self.assertEqual(
            "2026-07-29T01:00:00+00:00",
            normalized["capture_policy"]["start"],
        )
        equivalent = feishu_chat_profile(since_last=False)
        equivalent["capture_policy"]["start"] = "2026-07-29T01:00:00Z"
        equivalent["capture_policy"]["end"] = "2026-07-29T10:00:00Z"
        self.assertEqual(
            profile_revision(explicit),
            profile_revision(equivalent),
        )

    def test_v2_feishu_chat_rejects_ambiguous_windows(self):
        invalid = feishu_chat_profile()
        invalid["capture_policy"]["start"] = "2026-07-29T09:00:00+08:00"
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile(since_last=False)
        del invalid["capture_policy"]["end"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile(since_last=False)
        invalid["capture_policy"]["overlap_seconds"] = 1
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile(since_last=False)
        invalid["capture_policy"]["end"] = invalid["capture_policy"]["start"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile(since_last=False)
        invalid["capture_policy"]["start"] = "2026-07-29T09:00:00"
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile()
        invalid["source_uid"] = "oc_other"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual(
            "SOURCE_PROFILE_IDENTITY_MISMATCH",
            caught.exception.code,
        )

        invalid = feishu_chat_profile()
        invalid["capture_policy"]["max_pages"] = 60
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile()
        invalid["capture_policy"]["page_size"] = 51
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_chat_profile(since_last=False)
        invalid["routine"] = {"enabled": True, "cadence": "daily"}
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

    def test_v2_rejects_unknown_types_fields_and_nested_credentials(self):
        invalid = meego_profile()
        invalid["source_type"] = "feishu_minutes"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual("SOURCE_PROFILE_UNSUPPORTED", caught.exception.code)

        invalid = meego_profile()
        invalid["unexpected"] = "value"
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = meego_profile()
        invalid["capture_policy"]["nested"] = {
            "settings": [{"client_secret": "must-not-be-saved"}]
        }
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual(
            "SOURCE_PROFILE_CONTAINS_CREDENTIAL",
            caught.exception.code,
        )

    def test_v2_feishu_wiki_is_subtree_scoped_and_strict(self):
        normalized = validate_profile(feishu_wiki_profile())
        self.assertEqual(
            "feishu_wiki:space-1:node-1",
            normalized["source_uid"],
        )
        self.assertEqual(
            ["doc", "docx"],
            normalized["capture_policy"]["include_types"],
        )
        invalid = feishu_wiki_profile()
        invalid["selector"]["root_node_token"] = "other"
        with self.assertRaises(SourceProfileError) as caught:
            validate_profile(invalid)
        self.assertEqual("SOURCE_PROFILE_IDENTITY_MISMATCH", caught.exception.code)

        invalid = feishu_wiki_profile()
        invalid["capture_policy"]["change_detection"] = "full_refresh"
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

        invalid = feishu_wiki_profile()
        invalid["capture_policy"]["include_types"] = ["docx", "sheet"]
        with self.assertRaises(SourceProfileError):
            validate_profile(invalid)

    def test_load_and_list_support_v1_and_v2_stable_paths(self):
        values = [
            validate_profile(profile()),
            validate_profile(meego_profile()),
            validate_profile(feishu_doc_profile()),
            validate_profile(feishu_base_profile()),
            validate_profile(feishu_chat_profile()),
            validate_profile(feishu_wiki_profile()),
        ]
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            for value in values:
                path = kb / profile_relative_path(value)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(value, ensure_ascii=False),
                    encoding="utf-8",
                )
            self.assertEqual(
                {value["source_uid"] for value in values},
                {value["source_uid"] for value in list_profiles(kb)},
            )
            for value in values:
                self.assertEqual(
                    value,
                    load_profile(kb, value["source_uid"]),
                )
                self.assertEqual(
                    profile_relative_path(value),
                    profile_relative_path(load_profile(kb, value["source_uid"])),
                )
            self.assertEqual(
                ["docx123"],
                [
                    value["source_uid"]
                    for value in list_profiles(kb, source_type="feishu_doc")
                ],
            )
            with self.assertRaises(SourceProfileError) as caught:
                list_profiles(kb, source_type="unknown")
            self.assertEqual("SOURCE_PROFILE_UNSUPPORTED", caught.exception.code)

    def test_list_profiles_ignores_unrelated_legacy_schedule_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            sources = kb / "sources"
            sources.mkdir()
            sources.joinpath("calendar-schedule.json").write_text(
                json.dumps(
                    {
                        "source_type": "feishu_calendar",
                        "source_uid": "calendar:weekly",
                        "schedule": {"cadence": "weekly"},
                    }
                ),
                encoding="utf-8",
            )
            value = validate_profile(feishu_doc_profile())
            path = kb / profile_relative_path(value)
            path.write_text(json.dumps(value), encoding="utf-8")

            self.assertEqual(
                ["docx123"],
                [item["source_uid"] for item in list_profiles(kb)],
            )

    def test_list_profiles_still_rejects_malformed_supported_profile(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            sources = kb / "sources"
            sources.mkdir()
            sources.joinpath("feishu_doc-invalid.json").write_text(
                json.dumps(
                    {
                        "source_type": "feishu_doc",
                        "source_uid": "docx123",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SourceProfileError) as caught:
                list_profiles(kb)
            self.assertEqual("SOURCE_PROFILE_INVALID", caught.exception.code)

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

    def test_save_profile_supports_v2(self):
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
            subprocess.run(["git", "commit", "-qm", "init"], cwd=kb, check=True)

            value = meego_profile()
            receipt = save_profile(
                kb,
                value,
                skill_root=ROOT,
                now=datetime(2026, 7, 29, 11, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
            )
            self.assertEqual("committed", receipt["status"])
            self.assertEqual(
                validate_profile(value),
                load_profile(kb, value["source_uid"]),
            )
            self.assertEqual(
                profile_revision(value),
                receipt["profile_revision"],
            )


if __name__ == "__main__":
    unittest.main()
