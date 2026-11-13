import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DigestTransactionContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (ROOT / relative_path).read_text(encoding="utf-8")


    def test_v2_provenance_anchors_belong_to_source_bundle(self):
        provenance = self.read("references/provenance.md")
        base = self.read("references/digest-base.md")
        aeolus = self.read("references/digest-aeolus.md")
        for text in (provenance, base, aeolus):
            self.assertIn("SourceBundle", text)
            self.assertIn("不得复制", text)
        self.assertIn("唯一运行配置", base)
        self.assertIn("不得从最近 raw", base)


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
