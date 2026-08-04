import sys
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_models import (  # noqa: E402
    ACTION_CLAIM_SCHEMA,
    ACTION_PLAN_SCHEMA,
    DREAMING_BATCH_SCHEMA,
    EVIDENCE_BATCH_SCHEMA,
    FINDING_BUNDLE_SCHEMA,
    DreamingModelError,
    validate_contract,
)


class DreamingModelTests(unittest.TestCase):
    def evidence(self) -> dict:
        return {
            "schema_version": EVIDENCE_BATCH_SCHEMA,
            "batch_id": "EB-1",
            "source": {
                "source_type": "feishu_chat",
                "principal": "user:ou_test",
                "lane": "monitored",
                "grant_revision": 2,
            },
            "window": {
                "requested_start": "2026-08-04T00:00:00+08:00",
                "requested_end": "2026-08-04T01:00:00+08:00",
            },
            "coverage": {"status": "complete", "gaps": []},
            "items": [
                {
                    "item_id": "message:om_test",
                    "anchor": {"kind": "message_id", "value": "om_test"},
                    "content_ref": "spool://EB-1/message-1",
                }
            ],
        }

    def test_valid_contracts(self):
        values = (
            self.evidence(),
            {
                "schema_version": DREAMING_BATCH_SCHEMA,
                "batch_id": "EB-1",
                "stage": "collected",
                "manifest_sha256": "a" * 64,
                "grant_revision": 2,
            },
            {
                "schema_version": FINDING_BUNDLE_SCHEMA,
                "batch_id": "EB-1",
                "findings": [
                    {
                        "finding_id": "F-1",
                        "kind": "risk",
                        "summary": "需要关注",
                        "why_it_matters": "可能影响交付",
                        "confidence": "medium",
                        "uncertainties": [],
                        "evidence_refs": ["message:om_test"],
                    }
                ],
            },
            {
                "schema_version": ACTION_PLAN_SCHEMA,
                "run_id": "RUN-1",
                "actions": [
                    {
                        "action_id": "A-1",
                        "kind": "include_report",
                        "dedupe_key": "report:F-1",
                        "finding_id": "F-1",
                        "evidence_refs": ["message:om_test"],
                        "policy_result": "allowed",
                        "requires_confirmation": False,
                        "requires_recapture": False,
                    }
                ],
            },
            {
                "schema_version": ACTION_CLAIM_SCHEMA,
                "action_id": "A-1",
                "run_id": "RUN-1",
                "job": "process",
                "period": "2026-08-04T01:00Z",
                "token": "token",
                "lease_epoch": 3,
                "status": "claimed",
            },
        )
        for value in values:
            with self.subTest(schema=value["schema_version"]):
                self.assertEqual(value, validate_contract(value))

    def test_unknown_schema_is_rejected(self):
        with self.assertRaises(DreamingModelError) as caught:
            validate_contract({"schema_version": "unknown"})
        self.assertEqual(
            "DREAMING_MODEL_SCHEMA_UNSUPPORTED",
            caught.exception.code,
        )

    def test_evidence_rejects_invalid_lane_and_coverage(self):
        for field, value in (("lane", "all"), ("coverage", "unknown")):
            evidence = self.evidence()
            if field == "lane":
                evidence["source"]["lane"] = value
            else:
                evidence["coverage"]["status"] = value
            with self.subTest(field=field):
                with self.assertRaises(DreamingModelError):
                    validate_contract(evidence)

    def test_finding_requires_evidence(self):
        value = {
            "schema_version": FINDING_BUNDLE_SCHEMA,
            "batch_id": "EB-1",
            "findings": [
                {
                    "finding_id": "F-1",
                    "kind": "risk",
                    "summary": "无证据结论",
                    "why_it_matters": "可能影响交付",
                    "confidence": "medium",
                    "uncertainties": [],
                    "evidence_refs": [],
                }
            ],
        }
        with self.assertRaises(DreamingModelError) as caught:
            validate_contract(value)
        self.assertEqual("DREAMING_MODEL_INVALID", caught.exception.code)

    def test_action_policy_result_and_claim_status_are_bounded(self):
        action = {
            "schema_version": ACTION_PLAN_SCHEMA,
            "run_id": "RUN-1",
            "actions": [
                {
                    "action_id": "A-1",
                    "kind": "include_report",
                    "dedupe_key": "report:F-1",
                    "finding_id": "F-1",
                    "evidence_refs": ["message:om_test"],
                    "policy_result": "maybe",
                    "requires_confirmation": False,
                    "requires_recapture": False,
                }
            ],
        }
        claim = {
            "schema_version": ACTION_CLAIM_SCHEMA,
            "action_id": "A-1",
            "run_id": "RUN-1",
            "job": "process",
            "period": "period",
            "token": "token",
            "lease_epoch": 1,
            "status": "done",
        }
        for value in (action, claim):
            with self.subTest(schema=value["schema_version"]):
                with self.assertRaises(DreamingModelError):
                    validate_contract(value)


if __name__ == "__main__":
    unittest.main()
