from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from semantic_policy import (  # noqa: E402
    SemanticPolicyError,
    validate_im_semantic,
)


def thread(**overrides):
    value = {
        "importance": 3,
        "relevance_to_user": 3,
        "reason_codes": ["project_status_change"],
        "should_include_report": True,
        "should_digest_kb": True,
        "title": "项目状态变化",
        "summary": "项目进入下一阶段",
        "facts": ["状态已变化"],
        "actions": [],
        "risks": [],
        "sources": [
            {
                "chat_id": "oc_1",
                "window": "2026-07-31T10:00:00+08:00..2026-07-31T10:30:00+08:00",
                "message_ids": ["om_1"],
            }
        ],
    }
    value.update(overrides)
    return value


class SemanticPolicyTests(unittest.TestCase):
    def value(self, item):
        return {
            "schema_version": "byteworker-im-semantic/v1",
            "threads": [item],
        }

    def test_validated_thresholds_and_evidence(self):
        result = validate_im_semantic(self.value(thread()))
        self.assertTrue(result["threads"][0]["should_digest_kb"])

    def test_report_and_digest_booleans_cannot_override_thresholds(self):
        with self.assertRaises(SemanticPolicyError) as caught:
            validate_im_semantic(
                self.value(
                    thread(
                        importance=2,
                        should_include_report=True,
                        should_digest_kb=False,
                    )
                )
            )
        self.assertEqual(
            "IM_SEMANTIC_THRESHOLD_MISMATCH",
            caught.exception.code,
        )

    def test_digest_requires_registered_reason_and_message_evidence(self):
        with self.assertRaises(SemanticPolicyError) as caught:
            validate_im_semantic(
                self.value(
                    thread(
                        reason_codes=["important_information"],
                        should_digest_kb=True,
                    )
                )
            )
        self.assertEqual(
            "IM_SEMANTIC_THRESHOLD_MISMATCH",
            caught.exception.code,
        )
        with self.assertRaises(SemanticPolicyError) as caught:
            validate_im_semantic(
                self.value(
                    thread(
                        sources=[
                            {
                                "chat_id": "oc_1",
                                "window": "window",
                                "message_ids": [],
                            }
                        ]
                    )
                )
            )
        self.assertEqual("IM_SEMANTIC_EVIDENCE_MISSING", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
