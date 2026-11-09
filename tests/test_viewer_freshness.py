import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ViewerNodeDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is required for viewer logic tests")

        viewer = (ROOT / "viewer/index.html").read_text(encoding="utf-8")
        start = viewer.index("function parseDateValue(value)")
        end = viewer.index("// ---- INDEX.md parsing", start)
        source = viewer[start:end]
        fixed_now = "2026-07-29T20:30:00+08:00"
        script = f"""
const vm = require('vm');
const RealDate = Date;
class FixedDate extends RealDate {{
  static now() {{ return RealDate.parse({json.dumps(fixed_now)}); }}
}}
const context = {{ Date: FixedDate, esc: String }};
vm.createContext(context);
vm.runInContext({json.dumps(source)}, context);
const result = vm.runInContext(`JSON.stringify({{
  today: [updatedLabel({{updated: '2026-07-29'}}), updatedChip({{updated: '2026-07-29'}})],
  yesterday: [updatedLabel({{updated: '2026-07-28'}}), updatedChip({{updated: '2026-07-28'}})],
  compact: [updatedLabel({{updated: '20260727'}}), updatedChip({{updated: '20260727'}})],
  threeDays: [updatedLabel({{updated: '2026/07/26'}}), updatedChip({{updated: '2026/07/26'}})],
  sevenDays: [updatedLabel({{updated: '2026-07-22'}}), updatedChip({{updated: '2026-07-22'}})],
  eightDays: [updatedLabel({{updated: '2026-07-21'}}), updatedChip({{updated: '2026-07-21'}})],
  timestamp: [
    updatedLabel({{updated: '2026-07-29T20:00:00+08:00'}}),
    updatedChip({{updated: '2026-07-29T20:00:00+08:00'}})
  ],
  verifiedOnly: updatedChip({{last_verified: '2026-01-01'}}),
  createdOnly: updatedChip({{created: '2026-02-01'}}),
  unknown: [updatedLabel({{updated: 'not-a-date'}}), updatedChip({{updated: 'not-a-date'}})]
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
        self.assertEqual("8天", self.result["eightDays"][0])

    def test_date_chips_use_the_same_class_regardless_of_age(self):
        for key in ("today", "yesterday", "threeDays", "sevenDays", "eightDays"):
            self.assertTrue(self.result[key][1].startswith('<span class="updated" '), key)

    def test_date_chip_labels_the_actual_metadata_field(self):
        self.assertIn('title="最近更新：2026-07-21"', self.result["eightDays"][1])
        self.assertIn('title="上次核实：2026-01-01"', self.result["verifiedOnly"])
        self.assertIn('title="创建日期：2026-02-01"', self.result["createdOnly"])

    def test_timestamp_values_keep_elapsed_time_precision(self):
        self.assertEqual("30分钟前", self.result["timestamp"][0])

    def test_invalid_values_remain_unknown(self):
        self.assertEqual("—", self.result["unknown"][0])
        self.assertIn('title="日期未知"', self.result["unknown"][1])


if __name__ == "__main__":
    unittest.main()
