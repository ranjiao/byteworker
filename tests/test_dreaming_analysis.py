import json
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_analysis import (  # noqa: E402
    load_finding_bundle,
    validate_finding_evidence,
)
from dreaming_batch import create_collected_batch  # noqa: E402
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402


def finding(batch_id, evidence="message:om_1"):
    return {
        "schema_version": "byteworker-finding-bundle/v1",
        "batch_id": batch_id,
        "findings": [
            {
                "finding_id": "F-risk",
                "kind": "risk",
                "summary": "存在交付风险",
                "why_it_matters": "可能影响项目上线",
                "evidence_refs": [evidence],
                "confidence": "medium",
                "uncertainties": ["尚未确认负责人"],
            }
        ],
    }


class DreamingAnalysisTests(unittest.TestCase):
    def make_batch(self, root: Path):
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        grant = set_im_grant(
            kb,
            mode="monitored",
            persist_finding=True,
            acknowledge_all_visible=False,
        )
        receipt = create_collected_batch(
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
            now=datetime(2026, 8, 4, 2, tzinfo=timezone.utc),
        )
        return kb, receipt["batch_id"]

    def test_validates_evidence_refs_and_persist_grant(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb, batch_id = self.make_batch(Path(temporary))
            result = validate_finding_evidence(
                kb,
                batch_id=batch_id,
                bundle=finding(batch_id),
            )
            self.assertTrue(result["persist_finding"])

            with self.assertRaises(DreamingError) as caught:
                validate_finding_evidence(
                    kb,
                    batch_id=batch_id,
                    bundle=finding(batch_id, "message:om_missing"),
                )
            self.assertEqual(
                "DREAMING_FINDING_EVIDENCE_INVALID",
                caught.exception.code,
            )

    def test_bundle_in_skill_repo_is_rejected(self):
        path = ROOT / ".forbidden-finding.json"
        with self.assertRaises(DreamingError) as caught:
            load_finding_bundle(path, skill_root=ROOT)
        self.assertEqual("DREAMING_OUTPUT_IN_SKILL_REPO", caught.exception.code)

    def test_load_bundle_enforces_schema(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            path = Path(temporary) / "finding.json"
            path.write_text(json.dumps({"schema_version": "bad"}), encoding="utf-8")
            with self.assertRaises(DreamingError):
                load_finding_bundle(path, skill_root=ROOT)


if __name__ == "__main__":
    unittest.main()
