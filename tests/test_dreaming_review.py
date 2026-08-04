from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_batch import create_collected_batch  # noqa: E402
from dreaming_consolidation import (  # noqa: E402
    consolidate_findings,
    explain_finding,
    load_findings,
    record_finding_feedback,
    review_findings,
)
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402


class DreamingReviewTests(unittest.TestCase):
    def setup_finding(self, root: Path):
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
                    "content": "TOP SECRET RAW",
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
                        "finding_id": "F-1",
                        "kind": "risk",
                        "summary": "存在风险",
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

    def test_review_explain_and_feedback_are_bounded_and_idempotent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.setup_finding(Path(temporary))
            review = review_findings(kb)
            self.assertEqual(1, review["count"])
            self.assertNotIn("evidence_refs", review["findings"][0])

            explained = explain_finding(kb, finding_id="F-1")
            self.assertEqual("F-1", explained["finding"]["finding_id"])
            self.assertNotIn("TOP SECRET RAW", str(explained))
            self.assertEqual(
                ["message:om_1"],
                explained["evidence"][0]["evidence_refs"],
            )

            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            first = record_finding_feedback(
                kb,
                finding_id="F-1",
                status="snoozed",
                value="already_known",
                request_id="REQ-1",
                snooze_until=(now + timedelta(days=1)).isoformat(),
                now=now,
            )
            second = record_finding_feedback(
                kb,
                finding_id="F-1",
                status="snoozed",
                value="already_known",
                request_id="REQ-1",
                snooze_until=(now + timedelta(days=1)).isoformat(),
                now=now,
            )
            self.assertEqual(first["event_id"], second["event_id"])
            self.assertEqual("snoozed", load_findings(kb)["findings"]["F-1"]["status"])
            self.assertEqual(1, review_findings(kb, status="snoozed")["count"])
            with self.assertRaises(DreamingError) as caught:
                record_finding_feedback(
                    kb,
                    finding_id="F-1",
                    status="dismissed",
                    value="unimportant",
                    request_id="REQ-1",
                    now=now,
                )
            self.assertEqual(
                "DREAMING_FINDING_FEEDBACK_CONFLICT",
                caught.exception.code,
            )


if __name__ == "__main__":
    unittest.main()
