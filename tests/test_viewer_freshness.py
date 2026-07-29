import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ViewerFreshnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is required for viewer logic tests")

        viewer = (ROOT / "viewer/index.html").read_text(encoding="utf-8")
        start = viewer.index("function parseDateValue(value)")
        end = viewer.index("\nfunction isStale(n)", start)
        source = viewer[start:end]
        fixed_now = "2026-07-29T20:30:00+08:00"
        script = f"""
const vm = require('vm');
const RealDate = Date;
class FixedDate extends RealDate {{
  static now() {{ return RealDate.parse({json.dumps(fixed_now)}); }}
}}
const context = {{ Date: FixedDate }};
vm.createContext(context);
vm.runInContext({json.dumps(source)}, context);
const result = vm.runInContext(`JSON.stringify({{
  today: [updatedLabel({{updated: '2026-07-29'}}), freshnessMeta({{updated: '2026-07-29'}})],
  yesterday: [updatedLabel({{updated: '2026-07-28'}}), freshnessMeta({{updated: '2026-07-28'}})],
  compact: [updatedLabel({{updated: '20260727'}}), freshnessMeta({{updated: '20260727'}})],
  threeDays: [updatedLabel({{updated: '2026/07/26'}}), freshnessMeta({{updated: '2026/07/26'}})],
  sevenDays: [updatedLabel({{updated: '2026-07-22'}}), freshnessMeta({{updated: '2026-07-22'}})],
  eightDays: [updatedLabel({{updated: '2026-07-21'}}), freshnessMeta({{updated: '2026-07-21'}})],
  timestamp: [
    updatedLabel({{updated: '2026-07-29T20:00:00+08:00'}}),
    freshnessMeta({{updated: '2026-07-29T20:00:00+08:00'}})
  ],
  unknown: [updatedLabel({{updated: 'not-a-date'}}), freshnessMeta({{updated: 'not-a-date'}})]
}})`, context);
process.stdout.write(result);
"""
        completed = subprocess.run(
            ["node", "-e", script],
            cwd=ROOT,
            env={**os.environ, "TZ": "Asia/Shanghai"},
            check=True,
            capture_output=True,
            text=True,
        )
        cls.result = json.loads(completed.stdout)

    def test_date_only_values_use_calendar_day_labels(self):
        self.assertEqual("今天", self.result["today"][0])
        self.assertEqual("昨天", self.result["yesterday"][0])
        self.assertEqual("2天", self.result["compact"][0])
        self.assertEqual("3天", self.result["threeDays"][0])
        self.assertEqual("7天", self.result["sevenDays"][0])
        self.assertEqual("超过7天", self.result["eightDays"][0])

    def test_date_only_values_use_calendar_day_freshness_buckets(self):
        self.assertEqual(
            {"className": "fresh-now", "label": "今天更新"},
            self.result["today"][1],
        )
        self.assertEqual("fresh-recent", self.result["yesterday"][1]["className"])
        self.assertEqual("fresh-recent", self.result["compact"][1]["className"])
        self.assertEqual("fresh-aging", self.result["threeDays"][1]["className"])
        self.assertEqual("fresh-aging", self.result["sevenDays"][1]["className"])
        self.assertEqual("fresh-stale", self.result["eightDays"][1]["className"])

    def test_timestamp_values_keep_elapsed_time_precision(self):
        self.assertEqual("30分钟前", self.result["timestamp"][0])
        self.assertEqual("fresh-now", self.result["timestamp"][1]["className"])
        self.assertEqual("24 小时内更新", self.result["timestamp"][1]["label"])

    def test_invalid_values_remain_unknown(self):
        self.assertEqual("—", self.result["unknown"][0])
        self.assertEqual("fresh-unknown", self.result["unknown"][1]["className"])


if __name__ == "__main__":
    unittest.main()
