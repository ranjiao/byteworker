from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_action_policy import evaluate_action_plan  # noqa: E402
from dreaming_batch import create_collected_batch  # noqa: E402
from dreaming_consolidation import consolidate_findings  # noqa: E402
from dreaming_grants import set_action_grants, set_im_grant  # noqa: E402


class DreamingActionPolicyTests(unittest.TestCase):
    def setup_finding(self, root: Path, *, lane="monitored", coverage="complete"):
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        grant = set_im_grant(
            kb,
            mode="all_visible" if lane == "discovery" else "monitored",
            persist_finding=True,
            acknowledge_all_visible=lane == "discovery",
        )
        batch = create_collected_batch(
            kb,
            source={
                "source_type": "feishu_chat",
                "principal": "user:ou_me",
                "lane": lane,
            },
            window={
                "requested_start": "2026-08-04T00:00:00+00:00",
                "requested_end": "2026-08-04T01:00:00+00:00",
            },
            coverage={"status": coverage, "gaps": []},
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
        return kb

    def action(self, kind, **extra):
        return {
            "action_id": f"A-{kind}",
            "kind": kind,
            "dedupe_key": f"{kind}:F-risk",
            "finding_id": "F-risk",
            "evidence_refs": ["message:om_1"],
            "policy_result": "allowed",
            "requires_confirmation": False,
            "requires_recapture": False,
            **extra,
        }

    def test_grants_confirmation_and_recapture_are_deterministic(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.setup_finding(Path(temporary))
            set_action_grants(
                kb,
                persist_report=True,
                archive=True,
                instant_alert=False,
            )
            result = evaluate_action_plan(
                kb,
                plan={
                    "run_id": "lease-token",
                    "actions": [
                        self.action("include_report", target="daily"),
                        self.action("todo_candidate"),
                        self.action("knowledge_candidate"),
                        self.action("instant_alert"),
                    ],
                },
            )
            actions = {value["kind"]: value for value in result["actions"]}
            self.assertEqual("allowed", actions["include_report"]["policy_result"])
            self.assertEqual("confirm", actions["todo_candidate"]["policy_result"])
            self.assertTrue(actions["todo_candidate"]["requires_confirmation"])
            self.assertEqual("allowed", actions["knowledge_candidate"]["policy_result"])
            self.assertTrue(actions["knowledge_candidate"]["requires_recapture"])
            self.assertEqual("denied", actions["instant_alert"]["policy_result"])

    def test_discovery_evidence_cannot_be_archived(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.setup_finding(Path(temporary), lane="discovery", coverage="best_effort")
            set_action_grants(
                kb,
                persist_report=False,
                archive=True,
                instant_alert=False,
            )
            result = evaluate_action_plan(
                kb,
                plan={
                    "run_id": "lease-token",
                    "actions": [self.action("knowledge_candidate")],
                },
            )
            action = result["actions"][0]
            self.assertEqual("denied", action["policy_result"])
            self.assertIn(
                "complete_monitored_evidence_required",
                action["policy_reasons"],
            )


if __name__ == "__main__":
    unittest.main()
