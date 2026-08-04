import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DreamingI3CliTests(unittest.TestCase):
    def test_process_commit_persists_validated_finding(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            fake = root / "lark-cli"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:3] == ['auth', 'status']:\n"
                " print(json.dumps({'ok':True,'data':{'identities':{'user':{'openId':'ou_me'}}}}))\n"
                "else:\n"
                " print(json.dumps({'ok':True,'data':{'messages':[{'message_id':'om_1','chat_id':'oc_1','create_time':'2020-01-01T00:10:00+00:00','sender':{'id':'ou_sender'},'content':'risk'}],'has_more':False,'page_token':''}}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            grant = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "grant",
                    "set-im",
                    "--kb",
                    str(kb),
                    "--mode",
                    "all_visible",
                    "--persist-finding",
                    "--acknowledge-all-visible",
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, grant.returncode, grant.stdout)
            prepared = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "process",
                    "prepare",
                    "--kb",
                    str(kb),
                    "--source",
                    "im",
                    "--start",
                    "2020-01-01T00:00:00+00:00",
                    "--end",
                    "2020-01-01T01:00:00+00:00",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={**os.environ, "BYTEWORKER_LARK_CLI_BIN": str(fake)},
            )
            self.assertEqual(
                0,
                prepared.returncode,
                prepared.stdout + prepared.stderr,
            )
            batch_id = json.loads(prepared.stdout)["batch_id"]
            bundle = root / "finding.json"
            bundle.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-finding-bundle/v1",
                        "batch_id": batch_id,
                        "findings": [
                            {
                                "finding_id": "F-risk",
                                "kind": "risk",
                                "summary": "交付风险",
                                "why_it_matters": "影响上线",
                                "evidence_refs": ["message:om_1"],
                                "confidence": "medium",
                                "uncertainties": [],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            committed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "process",
                    "commit",
                    "--kb",
                    str(kb),
                    "--batch-id",
                    batch_id,
                    "--input",
                    str(bundle),
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, committed.returncode, committed.stdout)
            result = json.loads(committed.stdout)
            self.assertTrue(result["persisted_findings"])
            findings = json.loads(
                (kb / "state" / "dreaming" / "findings.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("F-risk", findings["findings"])


if __name__ == "__main__":
    unittest.main()
