import json
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from kb_query import QueryError, evidence, search, source_records  # noqa: E402


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

    def write_snapshot(
        self,
        *,
        name,
        source_type,
        source_uid,
        ingested,
        records,
    ):
        raw_id = f"raw-{name}"
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": source_type,
            "source_uid": source_uid,
            "records": records,
        }
        path = self.kb / "raw_data" / f"{name}.md"
        path.write_text(
            f"""---
raw_id: {raw_id}
ingested: {ingested}
source_type: {source_type}
source_uid: {source_uid}
source_url: https://example.test/{name}
source_title: {name}
digest_status: digested
---

## 完整快照

```json
{json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))}
```
""",
            encoding="utf-8",
        )
        id_key = "work_item_id" if source_type == "meego" else "record_id"
        prefix = (
            "workitem"
            if source_type == "meego"
            else ("aeolus" if source_type == "aeolus" else "record")
        )
        anchors = []
        for record in records:
            container = (
                record.get("work_item_attribute", record)
                if source_type == "meego"
                else record
            )
            record_id = str(container.get(id_key, ""))
            if record_id:
                anchors.append(
                    {
                        "anchor_id": f"{prefix}:{record_id}",
                        "kind": (
                            "meego_workitem"
                            if source_type == "meego"
                            else (
                                "aeolus_report"
                                if source_type == "aeolus"
                                else "base_record"
                            )
                        ),
                        "precision": "exact",
                        "locator": {id_key: record_id},
                    }
                )
        (self.kb / "provenance" / f"{raw_id}.json").write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-provenance/v1",
                    "raw_id": raw_id,
                    "raw_path": str(path.relative_to(self.kb)),
                    "anchors": anchors,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return path

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

    def test_source_record_finds_nested_meego_record_by_id(self):
        self.write_snapshot(
            name="meego-latest",
            source_type="meego",
            source_uid="meego:project:view",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "work_item_attribute": {
                        "work_item_id": "wi-1001",
                        "work_item_name": "Alpha模型-样本清理-训练与验收",
                        "work_item_status": {"name": "执行阶段"},
                    },
                    "work_item_current_node": [{"name": "研发开发"}],
                }
            ],
        )
        result = source_records(
            self.kb,
            source_type="meego",
            record_id="wi-1001",
        )
        self.assertEqual(1, result["coverage"]["returned"])
        self.assertEqual("wi-1001", result["matches"][0]["record_id"])
        self.assertEqual(
            "workitem:wi-1001",
            result["matches"][0]["provenance"]["anchor_id"],
        )
        self.assertEqual(
            "exact",
            result["matches"][0]["provenance"]["anchor"]["precision"],
        )
        self.assertEqual([], result["coverage"]["missing_anchors"])
        self.assertEqual(
            "执行阶段",
            result["matches"][0]["record"]["work_item_attribute"][
                "work_item_status"
            ]["name"],
        )

    def test_source_record_title_normalizes_punctuation_and_supports_fuzzy_typo(self):
        self.write_snapshot(
            name="meego-title",
            source_type="meego",
            source_uid="meego:project:view",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "work_item_attribute": {
                        "work_item_id": "1",
                        "work_item_name": "Alpha模型-样本清理-训练与验收",
                    }
                },
                {
                    "work_item_attribute": {
                        "work_item_id": "2",
                        "work_item_name": "推荐模型资源治理",
                    }
                },
            ],
        )
        normalized = source_records(
            self.kb,
            title="Alpha模型 样本清理 训练与验收",
        )
        self.assertEqual("1", normalized["matches"][0]["record_id"])
        self.assertEqual(
            "normalized_exact", normalized["matches"][0]["match"]["kind"]
        )
        fuzzy = source_records(
            self.kb,
            title="Alpha模型 样本清里 训练验收",
            title_threshold=0.5,
        )
        self.assertEqual("1", fuzzy["matches"][0]["record_id"])
        self.assertGreater(fuzzy["matches"][0]["match"]["score"], 0.7)

    def test_source_record_title_does_not_treat_punctuation_as_a_query(self):
        self.write_snapshot(
            name="meego-punctuation",
            source_type="meego",
            source_uid="meego:project:view",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "work_item_attribute": {
                        "work_item_id": "1",
                        "work_item_name": "任意需求",
                    }
                }
            ],
        )
        with self.assertRaisesRegex(QueryError, "归一化后为空"):
            source_records(self.kb, title="-")

    def test_source_record_matches_base_title_field(self):
        self.write_snapshot(
            name="base-latest",
            source_type="feishu_base",
            source_uid="feishu_base:base:table:view",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "record_id": "rec1",
                    "fields": {
                        "需求名称": "多模态风险识别升级",
                        "状态": "进行中",
                    },
                }
            ],
        )
        result = source_records(
            self.kb,
            source_type="feishu_base",
            title="多模态 风险识别升级",
        )
        self.assertEqual("rec1", result["matches"][0]["record_id"])
        self.assertEqual(
            "fields.需求名称", result["matches"][0]["match"]["field"]
        )
        self.assertEqual(
            "record:rec1", result["matches"][0]["provenance"]["anchor_id"]
        )

    def test_source_record_matches_aeolus_report_id_and_title(self):
        self.write_snapshot(
            name="aeolus-latest",
            source_type="aeolus",
            source_uid="aeolus:cn:101:202:303",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "record_id": "report:401",
                    "report_id": 401,
                    "name": "示例指标卡片",
                    "rows": [{"满足率": "0.96"}],
                }
            ],
        )
        exact = source_records(
            self.kb,
            source_type="aeolus",
            record_id="report:401",
        )
        self.assertEqual("report:401", exact["matches"][0]["record_id"])
        self.assertEqual(
            "aeolus:report:401",
            exact["matches"][0]["provenance"]["anchor_id"],
        )
        by_title = source_records(
            self.kb,
            source_type="aeolus",
            title="示例指标卡片",
        )
        self.assertEqual(
            "report:401", by_title["matches"][0]["record_id"]
        )

    def test_source_record_defaults_to_latest_snapshot_and_history_is_explicit(self):
        self.write_snapshot(
            name="meego-old",
            source_type="meego",
            source_uid="meego:project:view",
            ingested="2026-07-28T14:00:00+08:00",
            records=[
                {
                    "work_item_attribute": {
                        "work_item_id": "old-only",
                        "work_item_name": "已经离开视图的需求",
                    }
                },
                {
                    "work_item_attribute": {
                        "work_item_id": "same",
                        "work_item_name": "旧标题",
                    }
                },
            ],
        )
        self.write_snapshot(
            name="meego-new",
            source_type="meego",
            source_uid="meego:project:view",
            ingested="2026-07-29T14:00:00+08:00",
            records=[
                {
                    "work_item_attribute": {
                        "work_item_id": "same",
                        "work_item_name": "新标题",
                    }
                }
            ],
        )
        latest = source_records(self.kb, record_id="same")
        self.assertEqual("新标题", latest["matches"][0]["title"])
        self.assertEqual(1, latest["coverage"]["selected_snapshots"])
        missing = source_records(self.kb, record_id="old-only")
        self.assertEqual([], missing["matches"])
        historical = source_records(self.kb, record_id="old-only", history=True)
        self.assertEqual(1, historical["coverage"]["returned"])
        self.assertFalse(
            historical["matches"][0]["provenance"]["is_latest_snapshot"]
        )


if __name__ == "__main__":
    unittest.main()
