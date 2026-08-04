import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_batch import create_collected_batch  # noqa: E402
from dreaming_consolidation import load_findings  # noqa: E402
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_process import commit_finding_bundle  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402


def bundle(batch_id, summary="风险"):
    return {
        "schema_version": "byteworker-finding-bundle/v1",
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


class DreamingProcessTests(unittest.TestCase):
    def make_batch(self, root: Path, *, persist: bool):
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        grant = set_im_grant(
            kb,
            mode="monitored",
            persist_finding=persist,
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
        )
        return kb, receipt["batch_id"]

    def write_bundle(self, root: Path, batch_id: str, summary="风险") -> Path:
        path = root / f"{batch_id}-finding.json"
        path.write_text(
            json.dumps(bundle(batch_id, summary), ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_persisted_commit_is_idempotent_and_conflict_safe(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb, batch_id = self.make_batch(root, persist=True)
            path = self.write_bundle(root, batch_id)
            first = commit_finding_bundle(
                kb,
                batch_id=batch_id,
                input_path=path,
                skill_root=ROOT,
            )
            second = commit_finding_bundle(
                kb,
                batch_id=batch_id,
                input_path=path,
                skill_root=ROOT,
            )
            self.assertTrue(first["persisted_findings"])
            self.assertEqual("already_committed", second["status"])
            self.assertIn("F-risk", load_findings(kb)["findings"])

            conflict = self.write_bundle(root, batch_id, "不同风险")
            with self.assertRaises(DreamingError) as caught:
                commit_finding_bundle(
                    kb,
                    batch_id=batch_id,
                    input_path=conflict,
                    skill_root=ROOT,
                )
            self.assertEqual(
                "DREAMING_FINDING_BUNDLE_CONFLICT",
                caught.exception.code,
            )

    def test_transient_commit_does_not_persist_finding(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb, batch_id = self.make_batch(root, persist=False)
            result = commit_finding_bundle(
                kb,
                batch_id=batch_id,
                input_path=self.write_bundle(root, batch_id),
                skill_root=ROOT,
            )
            self.assertFalse(result["persisted_findings"])
            self.assertEqual({}, load_findings(kb)["findings"])


if __name__ == "__main__":
    unittest.main()
