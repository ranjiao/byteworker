import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "lib"))

from digest_parallel import (  # noqa: E402
    DigestParallelError,
    merge_parallel_results,
    plan_parallel_work,
)


class DigestParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.packet = self.root / "packet.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_packet(self, *, semantic_items=10, dependencies=0):
        value = {
            "schema_version": "byteworker-digest-analysis-packet/v1",
            "input_hash": "sha256:source",
            "identity": {"source_type": "feishu_doc", "source_uid": "doc-1"},
            "outline": ["one"],
            "dependency_candidates": [
                {
                    "candidate_id": f"dep-{index:03d}",
                    "reference": f"doc-id:{index}",
                    "contexts": ["context"],
                }
                for index in range(dependencies)
            ],
            "participant_ids": [],
            "sections": [
                {
                    "name": "body",
                    "kind": "body",
                    "heading": "",
                    "source_bytes": semantic_items * 20,
                    "text_items": [
                        {"path": f"$/items/{index}", "text": f"semantic text {index}"}
                        for index in range(semantic_items)
                    ],
                }
            ],
            "anchors": [
                {
                    "anchor_id": "doc:block:1",
                    "component": "body",
                    "kind": "doc_block",
                }
            ],
            "stats": {"semantic_item_count": semantic_items},
        }
        self.packet.write_text(json.dumps(value), encoding="utf-8")

    def load_plan(self, receipt):
        return json.loads(Path(receipt["plan_path"]).read_text(encoding="utf-8"))

    def test_semantic_large_packet_is_balanced_across_three_workers(self):
        self.write_packet(semantic_items=600)
        receipt = plan_parallel_work(self.packet, self.work, stage="semantic")
        plan = self.load_plan(receipt)

        self.assertEqual("parallel", receipt["mode"])
        self.assertEqual(3, receipt["shard_count"])
        self.assertEqual(600, sum(item["item_count"] for item in plan["shards"]))
        self.assertLessEqual(
            max(item["item_count"] for item in plan["shards"])
            - min(item["item_count"] for item in plan["shards"]),
            1,
        )
        for item in plan["shards"]:
            self.assertEqual(0o600, os.stat(item["path"]).st_mode & 0o777)

    def test_dependency_and_conflict_thresholds_are_conditional(self):
        self.write_packet(dependencies=11)
        inline = plan_parallel_work(self.packet, self.work / "dep-inline", stage="dependency")
        self.assertEqual("inline", inline["mode"])

        self.write_packet(dependencies=12)
        parallel = plan_parallel_work(self.packet, self.work / "dep-parallel", stage="dependency")
        self.assertEqual("parallel", parallel["mode"])
        self.assertEqual(2, parallel["shard_count"])

        conflict = self.root / "conflicts.json"
        conflict.write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-conflict-candidates/v1",
                    "source_match": {"nodes": []},
                    "queries": [
                        {"id": f"q-{index}", "coverage": {}, "candidates": []}
                        for index in range(8)
                    ],
                }
            ),
            encoding="utf-8",
        )
        conflict_receipt = plan_parallel_work(
            conflict, self.work / "conflict", stage="conflict"
        )
        self.assertEqual("parallel", conflict_receipt["mode"])
        self.assertEqual(2, conflict_receipt["shard_count"])

    def test_merge_requires_complete_dependency_coverage(self):
        self.write_packet(dependencies=12)
        receipt = plan_parallel_work(self.packet, self.work, stage="dependency")
        plan = self.load_plan(receipt)
        results = []
        for shard_entry in plan["shards"]:
            shard = json.loads(Path(shard_entry["path"]).read_text(encoding="utf-8"))
            result_path = self.root / f"{shard['shard_id']}-result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-digest-parallel-result/v1",
                        "stage": "dependency",
                        "input_hash": plan["input_hash"],
                        "shard_id": shard["shard_id"],
                        "records": [
                            {
                                "candidate_id": item["candidate_id"],
                                "disposition": "not_important",
                                "reason_code": "BACKGROUND_REFERENCE",
                            }
                            for item in shard["items"]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            results.append(result_path)

        output = self.root / "dependency-reduce.json"
        merged = merge_parallel_results(Path(receipt["plan_path"]), results, output)
        self.assertEqual(12, merged["record_count"])
        packet = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(2, packet["stats"]["shard_count"])

        with self.assertRaises(DigestParallelError) as caught:
            merge_parallel_results(Path(receipt["plan_path"]), results[:1], output)
        self.assertEqual("DIGEST_PARALLEL_RESULT_INCOMPLETE", caught.exception.code)

    def test_semantic_merge_flags_duplicate_keys_for_reducer(self):
        self.write_packet(semantic_items=600)
        receipt = plan_parallel_work(self.packet, self.work, stage="semantic")
        plan = self.load_plan(receipt)
        results = []
        for shard_entry in plan["shards"]:
            shard = json.loads(Path(shard_entry["path"]).read_text(encoding="utf-8"))
            section = shard["sections"][0]
            item = section["text_items"][0]
            result_path = self.root / f"{shard['shard_id']}-result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-digest-parallel-result/v1",
                        "stage": "semantic",
                        "input_hash": plan["input_hash"],
                        "shard_id": shard["shard_id"],
                        "records": [
                            {
                                "record_id": f"record-{shard['shard_index']}",
                                "record_type": "fact",
                                "dedupe_key": "same-fact",
                                "source_refs": [
                                    {"component": section["name"], "path": item["path"]}
                                ],
                                "payload": {"summary": "private"},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            results.append(result_path)
        output = self.root / "semantic-reduce.json"
        merged = merge_parallel_results(Path(receipt["plan_path"]), results, output)
        self.assertEqual(1, merged["duplicate_dedupe_key_count"])

    def test_conflict_merge_covers_queries_and_rejects_foreign_candidates(self):
        conflict = self.root / "conflicts.json"
        conflict.write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-conflict-candidates/v1",
                    "source_match": {"nodes": []},
                    "queries": [
                        {
                            "id": f"q-{index}",
                            "coverage": {},
                            "candidates": [{"id": f"node-{index}"}],
                        }
                        for index in range(8)
                    ],
                }
            ),
            encoding="utf-8",
        )
        receipt = plan_parallel_work(conflict, self.work, stage="conflict")
        plan = self.load_plan(receipt)
        results = []
        for shard_entry in plan["shards"]:
            shard = json.loads(Path(shard_entry["path"]).read_text(encoding="utf-8"))
            result_path = self.root / f"{shard['shard_id']}-result.json"
            result_path.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-digest-parallel-result/v1",
                        "stage": "conflict",
                        "input_hash": plan["input_hash"],
                        "shard_id": shard["shard_id"],
                        "records": [
                            {
                                "query_id": query["id"],
                                "disposition": "no_conflict",
                                "candidate_ids": [query["candidates"][0]["id"]],
                            }
                            for query in shard["items"]
                        ],
                    }
                ),
                encoding="utf-8",
            )
            results.append(result_path)

        output = self.root / "conflict-reduce.json"
        merged = merge_parallel_results(Path(receipt["plan_path"]), results, output)
        self.assertEqual(8, merged["record_count"])

        broken = json.loads(results[0].read_text(encoding="utf-8"))
        broken["records"][0]["candidate_ids"] = ["foreign-node"]
        results[0].write_text(json.dumps(broken), encoding="utf-8")
        with self.assertRaises(DigestParallelError):
            merge_parallel_results(Path(receipt["plan_path"]), results, output)


if __name__ == "__main__":
    unittest.main()
