import sys
import tempfile
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

from rebuild_index import render_raw_sections  # noqa: E402


class RoutineSourceIndexTests(unittest.TestCase):
    def test_source_profile_is_routine_truth_and_suppresses_legacy_raw(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            raw = kb / "raw_data"
            raw.mkdir()
            (raw / "snapshot.md").write_text(
                """---
raw_id: raw-aeolus
ingested: 2026-07-29T10:00:00+08:00
source_type: aeolus
source_uid: aeolus:cn:101:202:303
source_title: 旧 raw 标题
routine: daily
digest_status: digested
digest_targets:
  - reading-gpu
---
snapshot
""",
                encoding="utf-8",
            )
            sources = kb / "sources"
            sources.mkdir()
            value = {
                "schema_version": "byteworker-source-profile/v1",
                "source_type": "aeolus",
                "source_uid": "aeolus:cn:101:202:303",
                "source_url": "https://data.bytedance.net/aeolus/pages/dashboard/202?appId=101&sheetId=303",
                "title": "GPU 交付 / 月度 sheet",
                "coordinates": {
                    "region": "cn",
                    "app_id": 101,
                    "dashboard_id": 202,
                    "sheet_id": 303,
                },
                "capture": {
                    "report_selector": {"mode": "include", "report_ids": [401]},
                    "filters": {"mode": "dashboard", "where": []},
                    "max_items_per_report": 1000,
                },
                "routine": {"enabled": True, "cadence": "weekly"},
            }
            from source_profiles import profile_relative_path
            path = kb / profile_relative_path(value)
            path.write_text(json.dumps(value), encoding="utf-8")
            section, routine_count, _, _, _ = render_raw_sections(str(kb))
            text = "\n".join(section)
            self.assertEqual(1, routine_count)
            self.assertIn("GPU 交付 / 月度 sheet", text)
            self.assertIn("weekly", text)
            self.assertIn("2026-07-29", text)
            self.assertIn("reading-gpu", text)
            self.assertNotIn("旧 raw 标题", text)
            self.assertNotIn("daily", text)

    def test_disabled_profile_removes_legacy_raw_from_routine_index(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            raw = kb / "raw_data"
            raw.mkdir()
            (raw / "snapshot.md").write_text(
                """---
raw_id: raw-aeolus
ingested: 2026-07-29T10:00:00+08:00
source_type: aeolus
source_uid: aeolus:cn:101:202:303
source_title: Legacy
routine: weekly
digest_status: digested
---
snapshot
""",
                encoding="utf-8",
            )
            value = {
                "schema_version": "byteworker-source-profile/v1",
                "source_type": "aeolus",
                "source_uid": "aeolus:cn:101:202:303",
                "source_url": "https://data.bytedance.net/aeolus/pages/dashboard/202?appId=101&sheetId=303",
                "title": "Disabled",
                "coordinates": {
                    "region": "cn",
                    "app_id": 101,
                    "dashboard_id": 202,
                    "sheet_id": 303,
                },
                "capture": {
                    "report_selector": {"mode": "all", "report_ids": []},
                    "filters": {"mode": "dashboard", "where": []},
                    "max_items_per_report": 1000,
                },
                "routine": {"enabled": False, "cadence": None},
            }
            from source_profiles import profile_relative_path
            path = kb / profile_relative_path(value)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(value), encoding="utf-8")
            section, routine_count, _, _, _ = render_raw_sections(str(kb))
            self.assertEqual(0, routine_count)
            self.assertNotIn("Legacy", "\n".join(section))

    def test_snapshot_versions_are_grouped_by_stable_source_uid(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            raw = kb / "raw_data"
            raw.mkdir()
            for suffix, date, title, url in (
                ("v1", "2026-07-28", "旧标题", "https://example.test/base/old"),
                ("v2", "2026-07-29", "需求库 / 本周更新", "https://example.test/base/new"),
            ):
                (raw / f"{date}-{suffix}.md").write_text(
                    f"""---
raw_id: raw-{date}-{suffix}
ingested: {date}T10:00:00+08:00
source_type: feishu_base
source_uid: feishu_base:bas1:tbl1:vew1
source_url: {url}
source_title: {title}
routine: weekly
digest_status: digested
digest_targets:
  - reading-base-view
---

snapshot
""",
                    encoding="utf-8",
                )
            section, routine_count, _, _, _ = render_raw_sections(str(kb))
            text = "\n".join(section)
            self.assertEqual(1, routine_count)
            self.assertIn("需求库 / 本周更新", text)
            self.assertIn("多维表格", text)
            self.assertIn("2026-07-29", text)
            self.assertNotIn("旧标题", text)


if __name__ == "__main__":
    unittest.main()
