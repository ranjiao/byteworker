import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from kb_query import evidence, search  # noqa: E402


class KbQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.kb = Path(self.temp.name)
        (self.kb / "knowledge/projects").mkdir(parents=True)
        (self.kb / "knowledge/decisions").mkdir(parents=True)
        (self.kb / "raw_data").mkdir()
        (self.kb / "provenance").mkdir()
        (self.kb / "knowledge/projects/project-alpha.md").write_text(
            """---
id: project-alpha
title: Alpha 素材生成
type: project
tags: [agent]
sources: [raw-alpha]
links: [decision-alpha]
---

# Alpha

> **TL;DR:** 素材生成项目。
""",
            encoding="utf-8",
        )
        (self.kb / "knowledge/decisions/decision-alpha.md").write_text(
            """---
id: decision-alpha
title: Alpha 决策
type: decision
tags: [decision]
sources: [raw-alpha]
links: [project-alpha]
primary_source: raw-alpha
primary_source_url: https://example.test/alpha
---

# Alpha 决策

采用方案 A。[E1]

## 证据

| 编号 | 原始来源 | 定位 | 原文时间 | 收录时间 | 精度 |
|---|---|---|---|---|---|
| **[E1]** | [Alpha][E1] · `raw-alpha` | 来源级 · anchor_id=source |  | 2026-07-27 | source |

[E1]: <https://example.test/alpha>
""",
            encoding="utf-8",
        )
        (self.kb / "raw_data/raw-alpha.md").write_text(
            """---
raw_id: raw-alpha
source_type: web
source_uid: alpha
source_url: https://example.test/alpha
source_title: Alpha
ingested: 2026-07-27
content_hash: sha256:test
---

raw
""",
            encoding="utf-8",
        )
        (self.kb / "provenance/raw-alpha.json").write_text(
            """{
  "schema_version": "byteworker-provenance/v1",
  "raw_id": "raw-alpha",
  "raw_path": "raw_data/raw-alpha.md",
  "anchors": [
    {
      "anchor_id": "source",
      "kind": "source",
      "precision": "source_only",
      "open_url": "https://example.test/alpha",
      "locator": {}
    }
  ]
}
""",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_search_expands_one_graph_hop(self):
        result = search(self.kb, "素材生成", limit=1, graph_depth=1, max_nodes=3)
        self.assertEqual("project-alpha", result["candidates"][0]["id"])
        self.assertEqual(
            {"project-alpha", "decision-alpha"},
            {item["id"] for item in result["candidates"]},
        )
        self.assertEqual(2, result["coverage"]["scanned_nodes"])

    def test_evidence_resolves_raw_and_sidecar(self):
        result = evidence(self.kb, "decision-alpha", ["E1"])
        self.assertEqual("raw-alpha", result["evidence"][0]["raw_id"])
        self.assertEqual(
            "https://example.test/alpha", result["evidence"][0]["source_url"]
        )
        self.assertEqual("source", result["evidence"][0]["anchor"]["anchor_id"])


if __name__ == "__main__":
    unittest.main()
