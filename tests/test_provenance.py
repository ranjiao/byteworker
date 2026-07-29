import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_txn import DigestTxnError, execute_plan, validate_plan  # noqa: E402
from frontmatter import parse_file  # noqa: E402
from provenance import (  # noqa: E402
    ProvenanceError,
    extract_offline_anchors,
    materialize_node_provenance,
    normalize_anchor,
)


NODE_DIRS = ("people", "projects", "areas", "orgs", "events", "decisions", "readings")


def git(kb: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(kb), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return result.stdout.strip()


class ProvenanceMaterializationTests(unittest.TestCase):
    def node_text(self, marker="[E1]"):
        return f"""---
id: reading-example
title: 示例
type: reading
status: current
created: 2026-07-27
updated: 2026-07-27
last_verified: 2026-07-27
sources:
  - raw-2026-07-27-example
links: []
---

# 示例

## 核心观点

- 一个关键事实。{marker}
"""

    def raw_records(self):
        return {
            "raw-2026-07-27-example": {
                "relative_path": "raw_data/2026-07-27-example.md",
                "frontmatter": {
                    "raw_id": "raw-2026-07-27-example",
                    "source_title": "原始文档",
                    "source_url": "https://example.test/doc#block-1",
                    "ingested": "2026-07-27T10:00:00+08:00",
                },
            }
        }

    def evidence(self):
        return [
            {
                "id": "E1",
                "raw_id": "raw-2026-07-27-example",
                "anchor": {
                    "anchor_id": "doc:block:block-1",
                    "kind": "doc_block",
                    "precision": "exact",
                    "open_url": "https://example.test/doc#block-1",
                    "source_time": "2026-07-26",
                    "locator": {"block_id": "block-1", "heading": "结论"},
                },
            }
        ]

    def test_materializes_primary_source_and_evidence_table(self):
        result = materialize_node_provenance(
            self.node_text(),
            "knowledge/readings/reading-example.md",
            "raw-2026-07-27-example",
            "https://example.test/doc",
            self.evidence(),
            self.raw_records(),
        )
        fm, body = parse_file_from_text(result)
        self.assertEqual("raw-2026-07-27-example", fm["primary_source"])
        self.assertEqual("https://example.test/doc", fm["primary_source_url"])
        self.assertIn("## 证据", body)
        self.assertIn("[E1]: <https://example.test/doc#block-1>", body)
        self.assertIn("2026-07-27T10:00:00+08:00", body)

    def test_rejects_marker_mapping_mismatch(self):
        with self.assertRaisesRegex(ProvenanceError, "映射不一致"):
            materialize_node_provenance(
                self.node_text("[E2]"),
                "knowledge/readings/reading-example.md",
                "raw-2026-07-27-example",
                "https://example.test/doc",
                self.evidence(),
                self.raw_records(),
            )

    def test_rejects_unsafe_open_url(self):
        with self.assertRaisesRegex(ProvenanceError, "不安全"):
            normalize_anchor(
                "raw-2026-07-27-example",
                {
                    "anchor_id": "source",
                    "kind": "source",
                    "precision": "source_only",
                    "open_url": "javascript:alert(1)",
                    "locator": {"source_uid": "doc-1"},
                },
            )

    def test_accepts_exact_meego_and_base_record_anchors(self):
        for kind, locator in (
            (
                "meego_workitem",
                {"project_key": "proj", "view_id": "view", "work_item_id": "1"},
            ),
            (
                "base_record",
                {
                    "base_token": "bas1",
                    "table_id": "tbl1",
                    "view_id": "vew1",
                    "record_id": "rec1",
                },
            ),
        ):
            anchor = normalize_anchor(
                "raw-2026-07-29-source",
                {
                    "anchor_id": f"{kind}:1",
                    "kind": kind,
                    "precision": "exact",
                    "open_url": "https://example.test/source",
                    "locator": locator,
                },
            )
            self.assertEqual(kind, anchor["kind"])
            self.assertEqual(locator, anchor["locator"])


class ProvenanceOfflineExtractionTests(unittest.TestCase):
    def frontmatter(self):
        return {
            "raw_id": "raw-2026-07-27-example",
            "source_type": "feishu_doc",
            "source_title": "原始文档",
            "source_url": "https://example.test/docx/doc-1",
            "source_revision": "7",
            "ingested": "2026-07-27T10:00:00+08:00",
        }

    def test_extracts_modern_lark_block_ids(self):
        anchors = extract_offline_anchors(
            "raw-2026-07-27-example",
            self.frontmatter(),
            (
                '<h2 id="WgI1d6WFqoGVJaxnAf9caWoqnwh">背景</h2>\n'
                '<li id="XCPaddRN0oPlRyxHuRycRIfrngf">事实</li>\n'
                '<cite id="should-not-be-a-content-block">引用</cite>\n'
            ),
        )
        by_id = {anchor["anchor_id"]: anchor for anchor in anchors}
        self.assertIn(
            "doc:block:WgI1d6WFqoGVJaxnAf9caWoqnwh", by_id
        )
        self.assertIn(
            "doc:block:XCPaddRN0oPlRyxHuRycRIfrngf", by_id
        )
        self.assertNotIn(
            "doc:block:should-not-be-a-content-block", by_id
        )

    def test_extracts_comment_and_reply_with_block_locator(self):
        snapshot = [
            {
                "comment_id": "comment-1",
                "create_time": 1781086451,
                "user_id": "boss-user",
                "quote": "需要确认的原文",
                "relation": {
                    "relation": json.dumps(
                        {
                            "positionInfo": {
                                "blockID": "KNx5dnc8XomXBXxjJPmc0alFn1b"
                            }
                        }
                    )
                },
                "reply_list": {
                    "replies": [
                        {
                            "reply_id": "reply-1",
                            "create_time": "1781087451000",
                            "user_id": "owner-user",
                            "content": {
                                "elements": [
                                    {"text_run": {"text": "已确认"}},
                                    {"text_run": {"text": "，本周处理。"}},
                                ]
                            },
                        }
                    ]
                },
            }
        ]
        body = (
            '<p id="KNx5dnc8XomXBXxjJPmc0alFn1b">原文</p>\n\n'
            "## 文档评论原始快照\n\n"
            "```json\n"
            + json.dumps(snapshot, ensure_ascii=False)
            + "\n```\n"
        )
        anchors = extract_offline_anchors(
            "raw-2026-07-27-example", self.frontmatter(), body
        )
        by_id = {anchor["anchor_id"]: anchor for anchor in anchors}
        comment = by_id["doc:comment:comment-1"]
        self.assertEqual("doc_comment", comment["kind"])
        self.assertEqual(
            "KNx5dnc8XomXBXxjJPmc0alFn1b",
            comment["locator"]["block_id"],
        )
        self.assertEqual("boss-user", comment["author"])
        self.assertRegex(comment["source_time"], r"^\d{4}-\d{2}-\d{2}T")
        reply = by_id["doc:comment:comment-1:reply:reply-1"]
        self.assertEqual("doc_reply", reply["kind"])
        self.assertEqual("已确认 ，本周处理。", reply["quote"])
        self.assertEqual("owner-user", reply["author"])


def parse_file_from_text(text):
    from frontmatter import parse_frontmatter

    return parse_frontmatter(text)


class ProvenanceDigestTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.kb = self.root / "kb"
        self.inputs = self.root / "inputs"
        self.kb.mkdir()
        self.inputs.mkdir()
        (self.kb / "raw_data").mkdir()
        (self.kb / "journal").mkdir()
        for directory in NODE_DIRS:
            (self.kb / "knowledge" / directory).mkdir(parents=True, exist_ok=True)
        (self.kb / "INDEX.md").write_text("# 知识库索引\n", encoding="utf-8")
        git(self.kb, "init")
        git(self.kb, "config", "user.email", "test@example.test")
        git(self.kb, "config", "user.name", "Tests")
        git(self.kb, "config", "gc.auto", "0")
        git(self.kb, "config", "maintenance.auto", "false")
        git(self.kb, "add", "INDEX.md")
        git(self.kb, "commit", "-m", "init")

    def tearDown(self):
        self.temp.cleanup()

    def make_plan(self, marker="[E1]", anchor_id="doc:block:block-1"):
        body = self.inputs / "body.xml"
        body.write_text(
            '<heading id="block-1">结论</heading>\n<p>已完成。</p>\n',
            encoding="utf-8",
        )
        candidate = self.inputs / "candidate.md"
        candidate.write_text(
            f"""---
id: reading-example
title: 示例
type: reading
tags: [test]
status: current
created: 2026-07-27
updated: 2026-07-27
last_verified: 2026-07-27
sources:
  - raw-2026-07-27-example
links: []
---

# 示例

> **TL;DR:** 示例。

## 核心观点

- 已完成。{marker}
""",
            encoding="utf-8",
        )
        plan = {
            "schema_version": "digest-plan/v1",
            "source": {
                "type": "feishu_doc",
                "uid": "doc-1",
                "revision": "7",
                "url": "https://example.test/docx/doc-1",
                "title": "示例",
                "comments_status": "unavailable",
                "components": [
                    {
                        "name": "body",
                        "kind": "body",
                        "path": str(body),
                        "mode": "verbatim",
                    }
                ],
            },
            "raw": {
                "raw_id": "raw-2026-07-27-example",
                "path": "raw_data/2026-07-27-example.md",
            },
            "provenance": {
                "enrichment": "live",
                "anchors": [
                    {
                        "anchor_id": "doc:block:block-1",
                        "kind": "doc_block",
                        "precision": "exact",
                        "open_url": "https://example.test/docx/doc-1#block-1",
                        "locator": {"block_id": "block-1", "heading": "结论"},
                    }
                ],
            },
            "nodes": [
                {
                    "op": "create",
                    "path": "knowledge/readings/reading-example.md",
                    "candidate": str(candidate),
                    "primary_source": "raw-2026-07-27-example",
                    "evidence": [{"id": "E1", "anchor_id": anchor_id}],
                }
            ],
            "journal": {"summary": "《示例》"},
            "commit": {"message": "digest example"},
        }
        plan_path = self.inputs / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        return plan_path

    def test_execute_commits_raw_sidecar_and_evidence_node_together(self):
        receipt = execute_plan(self.kb, self.make_plan(), ROOT)
        self.assertEqual("committed", receipt["status"])
        self.assertEqual(1, receipt["evidence_count"])
        sidecar = self.kb / "provenance/raw-2026-07-27-example.json"
        self.assertTrue(sidecar.is_file())
        document = json.loads(sidecar.read_text(encoding="utf-8"))
        self.assertEqual("byteworker-provenance/v1", document["schema_version"])
        node = self.kb / "knowledge/readings/reading-example.md"
        fm, body = parse_file(str(node))
        self.assertEqual("raw-2026-07-27-example", fm["primary_source"])
        self.assertIn("## 证据", body)
        self.assertEqual("", git(self.kb, "status", "--short"))

    def test_unknown_anchor_fails_before_write(self):
        plan = self.make_plan(anchor_id="doc:block:missing")
        with self.assertRaisesRegex(DigestTxnError, "找不到 evidence anchor"):
            validate_plan(self.kb, plan)
        self.assertFalse((self.kb / "provenance").exists())

    def test_provenance_is_rolled_back_with_failed_transaction(self):
        plan = self.make_plan()
        with self.assertRaisesRegex(DigestTxnError, "INDEX 重建失败"):
            execute_plan(self.kb, plan, self.root / "missing-skill")
        self.assertFalse(
            (self.kb / "provenance/raw-2026-07-27-example.json").exists()
        )
        self.assertFalse((self.kb / "raw_data/2026-07-27-example.md").exists())
        self.assertEqual("", git(self.kb, "status", "--short"))


if __name__ == "__main__":
    unittest.main()
