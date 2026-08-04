import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_collection import prepare_foreground_im_batch  # noqa: E402
from dreaming_consolidation import load_findings  # noqa: E402
from dreaming_grants import foreground_session, get_im_grant  # noqa: E402
from dreaming_grants import expire_foreground_sessions  # noqa: E402
from dreaming_process import commit_finding_bundle  # noqa: E402
from dreaming_scheduler import status  # noqa: E402
from dreaming_state import DreamingError  # noqa: E402
from dreaming_state import load_state_unlocked, state_lock  # noqa: E402


class FakeCollector:
    def principal(self):
        return "user:ou_me"

    def collect_discovery(self, *, start, end):
        return {
            "lane": "discovery",
            "messages": [
                {
                    "message_id": "om_1",
                    "chat_id": "oc_p2p",
                    "chat_type": "p2p",
                    "create_time": "2026-08-04T00:10:00+00:00",
                    "sender": {"id": "ou_sender"},
                    "content": "secret",
                }
            ],
            "coverage": {"status": "best_effort", "gaps": []},
        }


class DreamingForegroundTests(unittest.TestCase):
    def test_disabled_foreground_does_not_enable_or_inherit_persistence(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            prepared = prepare_foreground_im_batch(
                kb,
                start="2026-08-04T00:00:00+00:00",
                end="2026-08-04T01:00:00+00:00",
                mode="all_visible",
                acknowledge_all_visible=True,
                collector=FakeCollector(),
                now=now,
            )
            self.assertTrue(prepared["foreground"])
            self.assertFalse(status(kb, now=now)["enabled"])
            self.assertEqual("off", get_im_grant(kb)["mode"])

            bundle = root / "finding.json"
            bundle.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-finding-bundle/v1",
                        "batch_id": prepared["batch_id"],
                        "findings": [
                            {
                                "finding_id": "F-1",
                                "kind": "risk",
                                "summary": "风险",
                                "why_it_matters": "影响交付",
                                "evidence_refs": ["message:om_1"],
                                "confidence": "medium",
                                "uncertainties": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            committed = commit_finding_bundle(
                kb,
                batch_id=prepared["batch_id"],
                input_path=bundle,
                skill_root=ROOT,
                now=now,
            )
            self.assertFalse(committed["persisted_findings"])
            self.assertEqual({}, load_findings(kb)["findings"])
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            token = next(iter(state["foreground_sessions"]))
            with self.assertRaises(DreamingError):
                foreground_session(
                    kb,
                    token=token,
                    now=now,
                )
            self.assertNotIn("foreground_sessions", status(kb, now=now))

    def test_all_visible_requires_foreground_ack(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary) / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            with self.assertRaises(DreamingError) as caught:
                prepare_foreground_im_batch(
                    kb,
                    start="2026-08-04T00:00:00+00:00",
                    end="2026-08-04T01:00:00+00:00",
                    mode="all_visible",
                    acknowledge_all_visible=False,
                    collector=FakeCollector(),
                    now=datetime(2026, 8, 4, 2, tzinfo=timezone.utc),
                )
            self.assertEqual("DREAMING_GRANT_ACK_REQUIRED", caught.exception.code)

    def test_expired_session_aborts_unfinished_batch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            prepared = prepare_foreground_im_batch(
                kb,
                start="2026-08-04T00:00:00+00:00",
                end="2026-08-04T01:00:00+00:00",
                mode="all_visible",
                acknowledge_all_visible=True,
                collector=FakeCollector(),
                now=now,
            )
            expired = expire_foreground_sessions(
                kb,
                now=now + timedelta(hours=3),
            )
            self.assertEqual(1, expired["expired_sessions"])
            self.assertEqual(1, expired["aborted_batches"])
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            self.assertEqual(
                "aborted",
                state["runs"][prepared["batch_id"]]["stage"],
            )


if __name__ == "__main__":
    unittest.main()
