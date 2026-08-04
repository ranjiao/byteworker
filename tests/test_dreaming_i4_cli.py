import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_batch import create_collected_batch  # noqa: E402
from dreaming_consolidation import consolidate_findings  # noqa: E402
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_scheduler import enable, run_due  # noqa: E402


class DreamingI4CliTests(unittest.TestCase):
    def test_plan_claim_and_complete(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            grant = set_im_grant(
                kb,
                mode="monitored",
                persist_finding=True,
                acknowledge_all_visible=False,
            )
            batch = create_collected_batch(
                kb,
                source={
                    "source_type": "feishu_chat",
                    "principal": "user:ou_me",
                    "lane": "monitored",
                },
                window={
                    "requested_start": "2026-08-04T00:00:00+00:00",
                    "requested_end": "2026-08-04T01:00:00+00:00",
                },
                coverage={"status": "complete", "gaps": []},
                messages=[
                    {
                        "message_id": "om_1",
                        "chat_id": "oc_1",
                        "create_time": "2026-08-04T00:10:00+00:00",
                        "sender": {"id": "ou_sender"},
                        "content": "risk",
                    }
                ],
                grant_revision=grant["revision"],
            )
            consolidate_findings(
                kb,
                batch_id=batch["batch_id"],
                bundle={
                    "findings": [
                        {
                            "finding_id": "F-risk",
                            "kind": "risk",
                            "summary": "风险",
                            "why_it_matters": "影响交付",
                            "evidence_refs": ["message:om_1"],
                            "confidence": "medium",
                            "uncertainties": [],
                        }
                    ]
                },
                expected_grant_revision=grant["revision"],
            )
            now = datetime.now(timezone.utc)
            enable(
                kb,
                harness="test",
                timezone_name="Asia/Shanghai",
                acknowledge_machine_runtime=True,
                acknowledge_capability_tour=True,
                now=now - timedelta(hours=3),
            )
            lease = run_due(
                kb,
                owner="test",
                now=now - timedelta(minutes=1),
            )["lease"]
            plan = root / "action-plan.json"
            plan.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-action-plan/v1",
                        "run_id": lease["token"],
                        "actions": [
                            {
                                "action_id": "A-suppress",
                                "kind": "suppress",
                                "dedupe_key": "suppress:F-risk",
                                "finding_id": "F-risk",
                                "evidence_refs": ["message:om_1"],
                                "policy_result": "allowed",
                                "requires_confirmation": False,
                                "requires_recapture": False,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            planned = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "action",
                    "plan",
                    "--kb",
                    str(kb),
                    "--input",
                    str(plan),
                    "--lease-token",
                    lease["token"],
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, planned.returncode, planned.stdout)
            claimed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "action",
                    "claim",
                    "--kb",
                    str(kb),
                    "--action-id",
                    "A-suppress",
                    "--lease-token",
                    lease["token"],
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            claim = json.loads(claimed.stdout)
            self.assertEqual(0, claimed.returncode, claimed.stdout)
            receipt = root / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "status": "noop",
                        "idempotency_key": "suppress:F-risk",
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "action",
                    "complete",
                    "--kb",
                    str(kb),
                    "--action-id",
                    "A-suppress",
                    "--claim-token",
                    claim["token"],
                    "--receipt",
                    str(receipt),
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, completed.returncode, completed.stdout)
            self.assertEqual("committed", json.loads(completed.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
