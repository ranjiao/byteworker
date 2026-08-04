from datetime import datetime, timezone
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
    load_findings,
    rebuild_findings_projection,
)
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_state import secure_path  # noqa: E402


def bundle(batch_id, summary="风险"):
    return {
        "batch_id": batch_id,
        "findings": [
            {
                "finding_id": "F-risk",
                "kind": "risk",
                "summary": summary,
                "why_it_matters": "影响交付",
                "evidence_refs": ["message:om_1"],
                "confidence": "medium",
                "uncertainties": [],
            }
        ],
    }


class DreamingConsolidationTests(unittest.TestCase):
    def make_batch(
        self,
        root: Path,
        grant_revision: int,
        *,
        lane: str = "monitored",
        message_id: str = "om_1",
    ):
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True, exist_ok=True)
        receipt = create_collected_batch(
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
            coverage={"status": "complete", "gaps": []},
            messages=[
                {
                    "message_id": message_id,
                    "chat_id": "oc_1",
                    "create_time": "2026-08-04T00:10:00+00:00",
                    "sender": {"id": "ou_sender"},
                    "content": "risk",
                }
            ],
            grant_revision=grant_revision,
        )
        return kb, receipt["batch_id"]

    def test_idempotent_history_and_projection_rebuild(self):
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
            kb, batch_id = self.make_batch(root, grant["revision"])
            first = consolidate_findings(
                kb,
                batch_id=batch_id,
                bundle=bundle(batch_id),
                expected_grant_revision=grant["revision"],
            )
            second = consolidate_findings(
                kb,
                batch_id=batch_id,
                bundle=bundle(batch_id),
                expected_grant_revision=grant["revision"],
            )
            self.assertEqual(1, first["appended_events"])
            self.assertEqual(0, second["appended_events"])
            projection = load_findings(kb)
            self.assertEqual(1, projection["findings"]["F-risk"]["revision"])

            secure_path(kb, "findings.json").unlink()
            rebuilt = rebuild_findings_projection(kb)
            self.assertEqual({"events": 1, "findings": 1}, rebuilt)

    def test_grant_revoke_purges_batch_findings(self):
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
            kb, batch_id = self.make_batch(root, grant["revision"])
            consolidate_findings(
                kb,
                batch_id=batch_id,
                bundle=bundle(batch_id),
                expected_grant_revision=grant["revision"],
            )
            set_im_grant(
                kb,
                mode="off",
                persist_finding=False,
                acknowledge_all_visible=False,
            )
            self.assertEqual({}, load_findings(kb)["findings"])

    def test_purge_recomputes_projection_without_revoked_evidence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            grant = set_im_grant(
                kb,
                mode="all_visible",
                persist_finding=True,
                acknowledge_all_visible=True,
            )
            kb, discovery = self.make_batch(
                root,
                grant["revision"],
                lane="discovery",
                message_id="om_disc",
            )
            discovery_bundle = bundle(discovery)
            discovery_bundle["findings"][0]["evidence_refs"] = ["message:om_disc"]
            consolidate_findings(
                kb,
                batch_id=discovery,
                bundle=discovery_bundle,
                expected_grant_revision=grant["revision"],
            )
            kb, monitored = self.make_batch(
                root,
                grant["revision"],
                lane="monitored",
                message_id="om_mon",
            )
            monitored_bundle = bundle(monitored)
            monitored_bundle["findings"][0]["evidence_refs"] = ["message:om_mon"]
            consolidate_findings(
                kb,
                batch_id=monitored,
                bundle=monitored_bundle,
                expected_grant_revision=grant["revision"],
            )
            set_im_grant(
                kb,
                mode="monitored",
                persist_finding=True,
                acknowledge_all_visible=False,
            )
            finding = load_findings(kb)["findings"]["F-risk"]
            self.assertEqual(["message:om_mon"], finding["evidence_refs"])
            self.assertEqual([monitored], finding["batch_ids"])


if __name__ == "__main__":
    unittest.main()
