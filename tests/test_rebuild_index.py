import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "bin"
if str(BIN) not in sys.path:
    sys.path.insert(0, str(BIN))

from rebuild_index import render_raw_sections  # noqa: E402


class RoutineSourceIndexTests(unittest.TestCase):
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
