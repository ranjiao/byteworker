import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]

import sys

sys.path.insert(0, str(ROOT / "lib"))

from digest_parallel import (  # noqa: E402
    DigestParallelError,
    attempt_parallel_shard,
    checkpoint_parallel_results,
    merge_parallel_results,
    plan_parallel_work,
    replan_parallel_work,
)
from digest_run_log import start_run  # noqa: E402
from workflow_budget import estimate_tokens, inspect_budget  # noqa: E402


class DigestParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.packet = self.root / "packet.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_packet(
        self,
        *,
        semantic_items=10,
        dependencies=0,
        semantic_text_size=0,
        dependency_context_size=0,
    ):
        # Repeated "x" compresses heavily with tiktoken and no longer exercises
        # the parallel path. Use stable, varied identifiers with both counters.
        dependency_context = hashlib.shake_256(b"dependency-context-fixture").hexdigest(
            (dependency_context_size + 1) // 2
        )[:dependency_context_size]
        value = {
            "schema_version": "byteworker-digest-analysis-packet/v1",
            "input_hash": "sha256:source",
            "identity": {"source_type": "feishu_doc", "source_uid": "doc-1"},
            "outline": ["one"],
            "dependency_candidates": [
                {
                    "candidate_id": f"dep-{index:03d}",
                    "reference": f"doc-id:{index}",
                    "contexts": ["context " + dependency_context],
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
                        {
                            "path": f"$/items/{index}",
                            "text": f"semantic text {index} " + "x" * semantic_text_size,
                        }
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

    def test_semantic_large_packet_is_balanced_across_bounded_workers(self):
        self.write_packet(semantic_items=600, semantic_text_size=1000)
        receipt = plan_parallel_work(self.packet, self.work, stage="semantic")
        plan = self.load_plan(receipt)

        self.assertEqual("parallel", receipt["mode"])
        self.assertIn(receipt["shard_count"], range(2, 5))
        self.assertEqual(
            "PARALLEL_SIZE_THRESHOLD",
            receipt["selection_reason_code"],
        )
        self.assertIn("inline", receipt["estimates"])
        self.assertGreater(receipt["estimates"]["source_tokens"], 0)
        self.assertNotIn("estimated_wall_time_ms", receipt["estimates"]["inline"])
        self.assertEqual(600, sum(item["item_count"] for item in plan["shards"]))
        self.assertLessEqual(
            max(item["item_count"] for item in plan["shards"])
            - min(item["item_count"] for item in plan["shards"]),
            1,
        )
        for item in plan["shards"]:
            self.assertEqual(0o600, os.stat(item["path"]).st_mode & 0o777)

    def test_small_dependency_and_conflict_batches_stay_inline(self):
        self.write_packet(dependencies=12)
        inline = plan_parallel_work(self.packet, self.work / "dep-inline", stage="dependency")
        self.assertEqual("inline", inline["mode"])
        self.assertEqual("INSUFFICIENT_WORK_ITEMS", inline["selection_reason_code"])

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
        self.assertEqual("inline", conflict_receipt["mode"])
        self.assertEqual("INSUFFICIENT_WORK_ITEMS", conflict_receipt["selection_reason_code"])

    def test_large_dependency_batch_parallelizes_only_with_token_and_wall_benefit(self):
        self.write_packet(dependencies=64, dependency_context_size=2000)
        receipt = plan_parallel_work(
            self.packet, self.work / "dep-parallel", stage="dependency"
        )
        self.assertEqual("parallel", receipt["mode"])
        self.assertEqual(
            "PARALLEL_SIZE_THRESHOLD",
            receipt["selection_reason_code"],
        )

    def test_large_conflict_batch_is_parallel_but_never_exceeds_four_workers(self):
        conflict = self.root / "large-conflicts.json"
        conflict.write_text(
            json.dumps(
                {
                    "schema_version": "byteworker-conflict-candidates/v1",
                    "source_match": {"nodes": []},
                    "queries": [
                        {
                            "id": f"q-{index}",
                            "coverage": {},
                            "candidates": [
                                {
                                    "id": f"node-{index}",
                                    "snippet": "x" * 4000,
                                }
                            ],
                        }
                        for index in range(64)
                    ],
                }
            ),
            encoding="utf-8",
        )
        receipt = plan_parallel_work(
            conflict, self.work / "large-conflict", stage="conflict"
        )
        self.assertEqual("parallel", receipt["mode"])
        self.assertLessEqual(receipt["shard_count"], 4)
        self.assertEqual(
            "PARALLEL_SIZE_THRESHOLD",
            receipt["selection_reason_code"],
        )

    def test_overflow_shards_preserve_text_and_reduce_in_bounded_batches(self):
        self.write_packet(semantic_items=1, dependencies=4)
        packet = json.loads(self.packet.read_text())
        text = "证据正文与原始定位\n" * 800
        packet["sections"][0]["text_items"][0]["text"] = text
        packet["anchors"][0]["quote"] = "保留在私有索引的引文" * 200
        self.packet.write_text(json.dumps(packet, ensure_ascii=False))

        def budgets(workflow, **_):
            limits = {"digest": 2000, "digest_semantic_worker": 1200, "digest_final_reducer": 1000}
            return 100, limits[workflow]

        with patch("digest_parallel._route_tokens", side_effect=budgets):
            receipt = plan_parallel_work(self.packet, self.work, stage="semantic", max_workers=1)
            plan = self.load_plan(receipt)
            self.assertTrue(receipt["budget_required"])
            self.assertEqual(1, receipt["max_workers"])
            self.assertGreater(receipt["shard_count"], 4)
            results = []
            cursor = 0
            pieces = []
            for index, entry in enumerate(plan["shards"]):
                path = Path(entry["path"])
                serialized = path.read_text()
                shard = json.loads(serialized)
                self.assertLessEqual(estimate_tokens(serialized)["tokens"], 1200)
                self.assertTrue(shard["anchors_deferred"])
                self.assertEqual([], shard["anchors"])
                item = shard["sections"][0]["text_items"][0]
                self.assertEqual(cursor, item["text_range"]["start"])
                cursor += len(item["text"])
                self.assertEqual(cursor, item["text_range"]["end"])
                pieces.append(item["text"])
                result = self.root / f"result-{index}.json"
                result.write_text(json.dumps({
                    "schema_version": "byteworker-digest-parallel-result/v1",
                    "stage": "semantic", "shard_id": shard["shard_id"], "input_hash": shard["input_hash"],
                    "records": [{
                        "record_id": f"fact-{index}", "record_type": "fact", "dedupe_key": "shared-key",
                        "source_refs": [{"component": "body", "path": item["path"]}],
                        "payload": {"description": "可核验记录" * 50},
                    }],
                }, ensure_ascii=False))
                results.append(result)
            self.assertEqual(text, "".join(pieces))
            self.assertEqual(packet, json.loads(self.packet.read_text()))
            with self.assertRaises(DigestParallelError):
                merge_parallel_results(Path(receipt["plan_path"]), results[:-1], self.root / "reduce.json")
            merged = merge_parallel_results(Path(receipt["plan_path"]), results, self.root / "reduce.json")
            batches = merged["reduction_batches"]
            self.assertGreater(len(batches), 1)
            self.assertEqual(len(results), sum(batch["record_count"] for batch in batches))
            for batch in batches:
                serialized = Path(batch["path"]).read_text()
                self.assertLessEqual(estimate_tokens(serialized)["tokens"], 1000)
                self.assertEqual(["shared-key"], json.loads(serialized)["duplicate_dedupe_keys"])

    def test_semantic_checkpoints_reuse_only_matching_input(self):
        self.write_packet(semantic_items=2)
        kb = self.root / "kb"
        folder = kb / "state/digest/flows"

        def plan(id):
            path = folder / f"DG-20261110T010101Z-{id:08x}" / "semantic"
            return Path(plan_parallel_work(self.packet, path, stage="semantic")["plan_path"])

        first = plan(1)
        data = json.loads(first.read_bytes())
        item = data["shards"][0]
        result = self.root / "worker-result.json"
        value = {
            "schema_version": "byteworker-digest-parallel-result/v1",
            "stage": "semantic", "shard_id": item["shard_id"], "input_hash": data["input_hash"],
            "records": [],
        }
        result.write_text(json.dumps({**value, "input_hash": "invalid"}), encoding="utf-8")
        with self.assertRaises(DigestParallelError):
            checkpoint_parallel_results(kb, first, result_path=result)
        self.assertEqual([item["shard_id"]], checkpoint_parallel_results(kb, first)["missing_shard_ids"])
        result.write_text(json.dumps(value), encoding="utf-8")
        saved = checkpoint_parallel_results(kb, first, result_path=result)["completed"][0]
        self.assertEqual(0o600, Path(saved["result_path"]).stat().st_mode & 0o777)

        packet = json.loads(self.packet.read_bytes())
        packet["generated_at"] = "later"
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        second = plan(2)
        resumed = checkpoint_parallel_results(kb, second)
        self.assertEqual([], resumed["missing_shard_ids"])
        paths = [Path(entry["result_path"]) for entry in resumed["completed"]]
        merge_parallel_results(second, paths, self.root / "reduce.json")

        packet["sections"][0]["text_items"][0]["text"] += " changed"
        self.packet.write_text(json.dumps(packet), encoding="utf-8")
        self.assertEqual([item["shard_id"]], checkpoint_parallel_results(kb, plan(3))["missing_shard_ids"])

    def test_host_replan_splits_pending_shards_and_preserves_completed_results(self):
        self.write_packet(semantic_items=28, semantic_text_size=1600)
        kb = self.root / "kb"
        stage = kb / "state/digest/flows/DG-20261110T010101Z-00000001/semantic"
        rules = inspect_budget(workflow="digest_semantic_worker")["measurement"]["total_input_tokens"]
        receipt = plan_parallel_work(self.packet, stage, stage="semantic", host_input_limit=rules + 3000)
        plan_path = Path(receipt["plan_path"])
        original = self.load_plan(receipt)
        self.assertGreater(len(original["shards"]), 1)
        completed = original["shards"][0]
        result = self.root / "worker-result.json"

        def save(shard_id):
            result.write_text(json.dumps({
                "schema_version": "byteworker-digest-parallel-result/v1", "stage": "semantic",
                "shard_id": shard_id, "input_hash": original["input_hash"], "records": [],
            }))
            return checkpoint_parallel_results(kb, plan_path, result_path=result)["completed"][0]

        saved = save(completed["shard_id"])
        before = plan_path.read_bytes()
        with self.assertRaises(DigestParallelError):
            replan_parallel_work(kb, plan_path, host_input_limit=rules)
        self.assertEqual(before, plan_path.read_bytes())
        replacement = replan_parallel_work(kb, plan_path, host_input_limit=rules + 1000)
        new_plan = self.load_plan(replacement)
        self.assertEqual(1, new_plan["max_workers"])
        self.assertEqual(original["input_hash"], new_plan["input_hash"])
        self.assertEqual(completed, new_plan["shards"][0])
        progress = checkpoint_parallel_results(kb, plan_path)
        self.assertEqual([saved], progress["completed"])
        self.assertGreater(new_plan["shard_count"], original["shard_count"])
        replaced = original["shards"][1]["shard_id"]
        with self.assertRaises(DigestParallelError):
            save(replaced)
        for entry in new_plan["shards"][1:]:
            measured = inspect_budget(workflow="digest_semantic_worker", source_packet_path=Path(entry["path"]))
            self.assertLessEqual(measured["measurement"]["total_input_tokens"], rules + 1000)
            save(entry["shard_id"])
        progress = checkpoint_parallel_results(kb, plan_path)
        self.assertFalse(progress["missing_shard_ids"])
        original_text = self.packet.read_text()
        sections = json.loads(original_text)["sections"]
        expected = {item["path"]: item["text"] for section in sections for item in section["text_items"]}
        pieces = {key: [] for key in expected}
        for entry in new_plan["shards"]:
            shard = json.loads(Path(entry["path"]).read_text())
            for section in shard["sections"]:
                for item in section["text_items"]:
                    pieces[item["path"]].append((item.get("text_range", {}).get("start", 0), item["text"]))
        self.assertEqual(expected, {key: "".join(text for _, text in sorted(parts)) for key, parts in pieces.items()})

        packet = json.loads(original_text)
        packet["generated_at"] = "later"
        self.packet.write_text(json.dumps(packet))
        second_stage = stage.parent.parent / "DG-20261110T010101Z-00000002/semantic"
        second = plan_parallel_work(self.packet, second_stage, stage="semantic", host_input_limit=rules + 3000)
        second_path = Path(second["plan_path"])
        replan_parallel_work(kb, second_path, host_input_limit=rules + 1000)
        self.assertFalse(checkpoint_parallel_results(kb, second_path)["missing_shard_ids"])

    def test_context_retry_without_host_capacity_shrinks_repeatedly_and_preserves_progress(self):
        self.write_packet(semantic_items=24)
        packet = json.loads(self.packet.read_text())
        for item in packet["sections"][0]["text_items"]:
            item["text"] = hashlib.shake_256(item["path"].encode()).hexdigest(600)
        self.packet.write_text(json.dumps(packet))
        kb = self.root / "kb"
        kb.mkdir()
        subprocess.run(["git", "init", "-q", str(kb)], check=True)
        run_id = start_run(kb, source_type="feishu_doc")["run_id"]
        stage = kb / "state/digest/flows" / run_id / "semantic"
        rules = inspect_budget(workflow="digest_semantic_worker")["measurement"]["total_input_tokens"]
        plan = plan_parallel_work(self.packet, stage, stage="semantic", host_input_limit=rules + 3600)
        plan_path = Path(plan["plan_path"])
        original = self.load_plan(plan)
        result = self.root / "worker-result.json"

        def save(shard_id):
            result.write_text(json.dumps({
                "schema_version": "byteworker-digest-parallel-result/v1", "stage": "semantic",
                "shard_id": shard_id, "input_hash": original["input_hash"], "records": [],
            }))
            return checkpoint_parallel_results(kb, plan_path, result_path=result)

        saved = save(original["shards"][0]["shard_id"])["completed"]
        for index in range(2):
            old_limit = plan["host_input_limit"]
            plan_data = self.load_plan(plan)
            pending = checkpoint_parallel_results(kb, plan_path)["missing_shard_ids"]
            shard = max(
                (entry for entry in plan_data["shards"] if entry["shard_id"] in pending),
                key=lambda entry: Path(entry["path"]).stat().st_size,
            )
            checked = attempt_parallel_shard(kb, plan_path, shard_id=shard["shard_id"])
            with patch("digest_worker_host.read_host_failure", return_value={
                "error_code": "DIGEST_HOST_CONTEXT_LIMIT", "host": "doubaowork", "tool": "create_agent",
                "call_id": f"call-{index}", "provider_code": "context_length_exceeded",
            }):
                attempt_parallel_shard(kb, plan_path, shard_id=shard["shard_id"],
                                       attempt_id=checked["attempt_id"], failure="DIGEST_HOST_CONTEXT_LIMIT")
            plan = replan_parallel_work(kb, plan_path, failed_attempt_id=checked["attempt_id"])
            self.assertEqual(("replanned", "context_probe", 1),
                             (plan["status"], plan["host_input_limit_source"], plan["max_workers"]))
            self.assertLess(plan["host_input_limit"], old_limit)
            self.assertGreater(plan["shard_count"], plan_data["shard_count"])
            self.assertEqual(saved, checkpoint_parallel_results(kb, plan_path)["completed"])

        for shard_id in checkpoint_parallel_results(kb, plan_path)["missing_shard_ids"]:
            save(shard_id)
        progress = checkpoint_parallel_results(kb, plan_path)
        paths = [Path(entry["result_path"]) for entry in progress["completed"]]
        reducer_rules = inspect_budget(workflow="digest_final_reducer")["measurement"]["total_input_tokens"]
        self.assertLess(plan["host_input_limit"], reducer_rules)
        merged = merge_parallel_results(plan_path, paths, self.root / "reduce.json")
        self.assertEqual(plan["shard_count"], merged["shard_count"])

    def test_merge_requires_complete_dependency_coverage(self):
        self.write_packet(dependencies=64, dependency_context_size=2000)
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
        self.assertEqual(64, merged["record_count"])
        packet = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(receipt["shard_count"], packet["stats"]["shard_count"])

        with self.assertRaises(DigestParallelError) as caught:
            merge_parallel_results(Path(receipt["plan_path"]), results[:1], output)
        self.assertEqual("DIGEST_PARALLEL_RESULT_INCOMPLETE", caught.exception.code)

    def test_semantic_merge_flags_duplicate_keys_and_ignores_legacy_todos(self):
        self.write_packet(semantic_items=600, semantic_text_size=1000)
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
                            },
                            {
                                "record_id": f"todo-{shard['shard_index']}",
                                "record_type": "todo_candidate",
                                "dedupe_key": "legacy-todo",
                                "source_refs": [
                                    {"component": section["name"], "path": item["path"]}
                                ],
                                "payload": {"summary": "legacy personal task"},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            results.append(result_path)
        output = self.root / "semantic-reduce.json"
        merged = merge_parallel_results(Path(receipt["plan_path"]), results, output)
        self.assertEqual(1, merged["duplicate_dedupe_key_count"])
        packet = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(["same-fact"], packet["duplicate_dedupe_keys"])
        self.assertEqual(len(plan["shards"]), len(packet["records"]))
        self.assertTrue(all(item["record_type"] == "fact" for item in packet["records"]))

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
