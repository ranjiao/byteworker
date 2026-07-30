import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DigestTransactionContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")

    def test_skill_routes_standard_digest_to_transaction_tool(self):
        skill = self.read("SKILL.md")
        self.assertIn("references/digest-transaction.md", skill)
        self.assertIn("bin/digest-txn.py preflight / validate / execute", skill)
        self.assertIn("语义判断、冲突", skill)

    def test_core_requires_receipt_before_claiming_write_completed(self):
        core = self.read("references/digest-core.md")
        self.assertIn("status=committed", core)
        self.assertIn("base_sha256", core)
        self.assertIn("digest-batch-plan/v2", core)
        self.assertIn("不得仅凭 Agent已生成候选就声称落库", core)

    def test_meeting_flow_links_bundle_contract_and_batch_v2_template(self):
        meeting = self.read("references/digest-meeting.md")
        machine = self.read("references/machine-protocol.md")
        self.assertIn("source bundle-spec --source-type feishu_minutes", meeting)
        self.assertIn("templates/digest-batch-plan-v2.json", meeting)
        self.assertIn("mode:\"verbatim\"", meeting)
        self.assertIn("SourceBundle request 快速参考", machine)
        self.assertTrue((ROOT / "templates/digest-batch-plan-v2.json").is_file())

    def test_schema_keeps_old_raw_compatible(self):
        design = self.read("DESIGN.md")
        transaction = self.read("references/digest-transaction.md")
        self.assertIn("byteworker-payload-v1", design)
        self.assertIn("旧 raw 永不改写", transaction)
        self.assertIn("不做启动时全库迁移", transaction)

    def test_whiteboard_rules_separate_structure_and_visual_inference(self):
        whiteboard = self.read("references/digest-whiteboard.md")
        self.assertIn("结构化节点 JSON", whiteboard)
        self.assertIn("整体预览图", whiteboard)
        self.assertIn("【视觉推断】", whiteboard)
        self.assertIn("不等于系统已上线", whiteboard)

    def test_business_manifest_is_forbidden_in_skill_repo(self):
        transaction = self.read("references/digest-transaction.md")
        write_rules = self.read("references/write-rules.md")
        self.assertIn("写进 skill 仓库", transaction)
        self.assertIn("禁止为单篇业务资料", write_rules)

    def test_cli_rejects_component_inside_skill_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "source.json"
            manifest.write_text(
                json.dumps(
                    {
                        "source": {
                            "type": "local_md",
                            "uid": "test",
                            "components": [
                                {
                                    "name": "body",
                                    "kind": "body",
                                    "path": str(ROOT / "templates/node-area.md"),
                                    "mode": "verbatim",
                                }
                            ],
                        }
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "python3",
                    str(ROOT / "bin/digest-txn.py"),
                    "preflight",
                    "--kb",
                    directory,
                    "--source",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(2, completed.returncode)
        self.assertIn("source component", completed.stdout)

    def test_cli_rejects_batch_v2_bundle_inside_skill_repo(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "batch.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "digest-batch-plan/v2",
                        "inputs": [
                            {
                                "source_bundle": str(
                                    ROOT / "templates/source-bundle-v2.json"
                                ),
                                "raw": {
                                    "raw_id": "raw-a",
                                    "path": "raw_data/a.md",
                                },
                            },
                            {
                                "source_bundle": str(
                                    ROOT / "templates/source-bundle-v2.json"
                                ),
                                "raw": {
                                    "raw_id": "raw-b",
                                    "path": "raw_data/b.md",
                                },
                            },
                        ],
                        "nodes": [],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    "python3",
                    str(ROOT / "bin/digest-txn.py"),
                    "validate",
                    "--kb",
                    directory,
                    "--plan",
                    str(manifest),
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(2, completed.returncode)
        self.assertIn("source bundle", completed.stdout)


if __name__ == "__main__":
    unittest.main()
