import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "byteworker-cli.py"

sys.path.insert(0, str(ROOT / "lib"))

from digest_analysis import (  # noqa: E402
    ANALYSIS_PACKET_SCHEMA,
    DigestAnalysisError,
    prepare_analysis_packet,
)
from digest_run_log import show_run, start_run  # noqa: E402


class DigestAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.body = self.root / "body.json"
        self.comments = self.root / "comments.json"
        self.whiteboard = self.root / "whiteboard.json"
        self.bundle = self.root / "bundle.json"
        self.packet = self.root / "analysis-packet.json"
        self.kb = self.root / "kb"
        self.kb.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)

        self.body.write_text(
            json.dumps(
                {
                    "data": {
                        "document": {
                            "content": (
                                '<h1>方案总览</h1><p>实现沿用<a '
                                'href="https://example.test/docx/dep-one">依赖方案</a>'
                                '</p><p><cite doc-id="dep-two">数据见附件</cite></p>'
                                '<p>重复<a href="https://example.test/docx/dep-one">链接</a></p>'
                            )
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.comments.write_text(
            json.dumps(
                {
                    "comments": [
                        {
                            "comment_id": "comment-1",
                            "user_id": "ou_1234567890abcdef1234567890abcdef",
                            "content": "需要说明回滚边界",
                            "quote": "实现沿用依赖方案",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.whiteboard.write_text(
            json.dumps(
                {
                    "nodes": [
                        {
                            "id": "node-1",
                            "type": "text",
                            "x": 123,
                            "y": 456,
                            "text": {"text": "离线数据流"},
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.bundle.write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-source-bundle/v2",
                    "identity": {
                        "source_type": "feishu_doc",
                        "source_uid": "doc-main",
                        "source_url": "https://example.test/docx/main",
                        "title": "Private title",
                        "revision": "7",
                    },
                    "components": [
                        {
                            "name": "body",
                            "kind": "body",
                            "path": str(self.body),
                            "mode": "verbatim",
                            "json_pointer": "/data/document/content",
                        },
                        {
                            "name": "comments",
                            "kind": "comments",
                            "path": str(self.comments),
                            "mode": "canonical-json",
                            "json_pointer": "/comments",
                        },
                        {
                            "name": "whiteboard:1",
                            "kind": "whiteboard",
                            "path": str(self.whiteboard),
                            "mode": "canonical-json",
                        },
                    ],
                    "coverage": {
                        "status": "complete",
                        "components": {
                            "body": "complete",
                            "comments": "complete",
                            "whiteboard:1": "complete",
                        },
                    },
                    "anchors": [
                        {
                            "anchor_id": "doc:block:1",
                            "kind": "doc_block",
                            "precision": "exact",
                            "locator": {"block_id": "1"},
                            "component": "body",
                        },
                        {
                            "anchor_id": "whiteboard:node:1",
                            "kind": "whiteboard_node",
                            "precision": "exact",
                            "locator": {"node_id": "node-1"},
                            "component": "whiteboard:1",
                        },
                    ],
                    "provider_metadata": {},
                    "snapshot_hash": None,
                    "payload_hash": None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_prepare_builds_one_compact_private_packet(self):
        receipt = prepare_analysis_packet(self.bundle, self.packet)
        self.assertEqual(ANALYSIS_PACKET_SCHEMA, receipt["schema_version"])
        self.assertFalse(receipt["cache_hit"])
        self.assertEqual(3, receipt["component_count"])
        self.assertEqual(2, receipt["dependency_candidate_count"])
        self.assertEqual(1, receipt["participant_count"])
        self.assertEqual(2, receipt["anchor_count"])
        self.assertNotIn("Private title", json.dumps(receipt))
        self.assertNotIn("example.test", json.dumps(receipt))
        self.assertEqual(0o600, os.stat(self.packet).st_mode & 0o777)

        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        self.assertEqual(["方案总览"], packet["outline"])
        self.assertEqual(
            [
                "doc-id:dep-two",
                "https://example.test/docx/dep-one",
            ],
            sorted(item["reference"] for item in packet["dependency_candidates"]),
        )
        self.assertTrue(
            all(item["relationship_hint"] for item in packet["dependency_candidates"])
        )
        whiteboard = next(
            item for item in packet["sections"] if item["kind"] == "whiteboard"
        )
        self.assertEqual(
            ["离线数据流"],
            [item["text"] for item in whiteboard["text_items"]],
        )
        self.assertNotIn("123", json.dumps(whiteboard, ensure_ascii=False))

    def test_prepare_reuses_same_packet_hash(self):
        first = prepare_analysis_packet(self.bundle, self.packet)
        second = prepare_analysis_packet(self.bundle, self.packet)
        self.assertEqual(first["input_hash"], second["input_hash"])
        self.assertTrue(second["cache_hit"])

    def test_prepare_does_not_truncate_long_semantic_text(self):
        tail = "TAIL-MARKER-MUST-SURVIVE"
        self.comments.write_text(
            json.dumps(
                {"comments": [{"content": ("long text " * 20_000) + tail}]},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        prepare_analysis_packet(self.bundle, self.packet)
        packet = json.loads(self.packet.read_text(encoding="utf-8"))
        comments = next(
            item for item in packet["sections"] if item["kind"] == "comments"
        )
        self.assertTrue(comments["text_items"][0]["text"].endswith(tail))

    def test_prepare_rejects_output_collision_with_component(self):
        original = self.body.read_bytes()
        with self.assertRaises(DigestAnalysisError) as caught:
            prepare_analysis_packet(self.bundle, self.body)
        self.assertEqual("DIGEST_ANALYSIS_OUTPUT_COLLISION", caught.exception.code)
        self.assertEqual(original, self.body.read_bytes())

    def test_prepare_rejects_business_output_inside_skill_repo(self):
        with self.assertRaises(DigestAnalysisError) as caught:
            prepare_analysis_packet(self.bundle, ROOT / "analysis-packet.json")
        self.assertEqual(
            "DIGEST_ANALYSIS_PATH_IN_SKILL_REPO",
            caught.exception.code,
        )

    def test_facade_returns_only_receipt_and_counts(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "digest-analysis",
                "prepare",
                "--bundle",
                str(self.bundle),
                "--out",
                str(self.packet),
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("success", payload["status"])
        self.assertEqual("digest-analysis", payload["context"]["tool"])
        self.assertEqual("prepare", payload["context"]["operation"])
        self.assertEqual(2, payload["data"]["dependency_candidate_count"])
        self.assertNotIn("Private title", completed.stdout)
        self.assertNotIn("example.test", completed.stdout)

    def test_prepare_run_id_records_analysis_stage_automatically(self):
        run_id = "DG-20260821T010203Z-abcdef12"
        start_run(
            self.kb,
            source_type="feishu_doc",
            run_id=run_id,
        )
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "bin" / "digest-analysis.py"),
                "prepare",
                "--kb",
                str(self.kb),
                "--bundle",
                str(self.bundle),
                "--out",
                str(self.packet),
                "--run-id",
                run_id,
            ],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, completed.returncode, msg=completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual("ok", payload["digest_run_logging"])
        self.assertEqual(run_id, payload["digest_run_id"])
        stages = show_run(self.kb, run_id=run_id)["summary"]["stages"]
        self.assertEqual(["analysis_prepare"], [item["stage"] for item in stages])
        self.assertEqual(3, stages[0]["metrics"]["component_count"])


if __name__ == "__main__":
    unittest.main()
