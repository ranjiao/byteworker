import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_batch import (  # noqa: E402
    abort_batch,
    commit_batch,
    create_collected_batch,
    gc_spool,
    recover_committed_cursors,
    write_stage_receipt,
)
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_state import (  # noqa: E402
    DreamingError,
    load_state_unlocked,
    save_state_unlocked,
    secure_path,
    state_lock,
)


class DreamingBatchTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        set_im_grant(
            kb,
            mode="monitored",
            persist_finding=False,
            acknowledge_all_visible=False,
        )
        return kb

    def create(self, kb: Path, now: datetime):
        return create_collected_batch(
            kb,
            source={
                "source_type": "feishu_chat",
                "principal": "user:ou_me",
                "lane": "monitored",
                "profile_ids": ["oc_test"],
            },
            window={
                "requested_start": "2026-08-04T00:00:00+00:00",
                "requested_end": "2026-08-04T01:00:00+00:00",
            },
            coverage={"status": "complete", "gaps": []},
            messages=[
                {
                    "message_id": "om_test",
                    "chat_id": "oc_test",
                    "create_time": "2026-08-04T00:30:00+00:00",
                    "sender": {"id": "ou_sender"},
                    "content": {"text": "secret"},
                }
            ],
            grant_revision=1,
            now=now,
        )

    def test_collected_batch_writes_private_manifest_and_spool(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            receipt = self.create(kb, datetime(2026, 8, 4, tzinfo=timezone.utc))
            batch_id = receipt["batch_id"]
            manifest = secure_path(kb, "batches", batch_id, "manifest.json")
            value = json.loads(manifest.read_text(encoding="utf-8"))
            self.assertEqual("byteworker-evidence-batch/v1", value["schema_version"])
            self.assertEqual(1, receipt["item_count"])
            content_ref = value["items"][0]["content_ref"]
            content = secure_path(
                kb,
                "spool",
                *content_ref.removeprefix("spool://").split("/"),
            )
            self.assertTrue(content.is_file())
            self.assertEqual(0o600, content.stat().st_mode & 0o777)
            self.assertNotIn("secret", json.dumps(receipt))

    def test_stale_grant_rejects_batch(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            set_im_grant(
                kb,
                mode="off",
                persist_finding=False,
                acknowledge_all_visible=False,
            )
            with self.assertRaises(DreamingError) as caught:
                self.create(kb, datetime(2026, 8, 4, tzinfo=timezone.utc))
            self.assertEqual("DREAMING_GRANT_STALE", caught.exception.code)

    def test_commit_requires_consolidation_and_recovery_repairs_cursor(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            receipt = self.create(kb, now)
            batch_id = receipt["batch_id"]
            with self.assertRaises(DreamingError):
                commit_batch(
                    kb,
                    batch_id=batch_id,
                    cursor_key="chat:oc_test",
                    through="om_test",
                )
            write_stage_receipt(
                kb,
                batch_id=batch_id,
                stage="analyzed",
                receipt={"ok": True},
            )
            write_stage_receipt(
                kb,
                batch_id=batch_id,
                stage="consolidated",
                receipt={"ok": True},
            )
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
                state["gaps"]["old-gap"] = {
                    "lane": "monitored",
                    "windows": [
                        {
                            "start": "2026-08-04T00:00:00+00:00",
                            "end": "2026-08-04T01:00:00+00:00",
                        }
                    ],
                }
                save_state_unlocked(kb, state)
            marker = commit_batch(
                kb,
                batch_id=batch_id,
                cursor_key="chat:oc_test",
                through="om_test",
            )
            self.assertEqual("committed", marker["stage"])
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
                self.assertNotIn("old-gap", state["gaps"])
                state["cursors"].pop("chat:oc_test")
                save_state_unlocked(kb, state)
            self.assertEqual(1, recover_committed_cursors(kb)["repaired"])

    def test_abort_and_gc(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            receipt = self.create(kb, now)
            aborted = abort_batch(
                kb,
                batch_id=receipt["batch_id"],
                error_code="MODEL_FAILED",
            )
            self.assertEqual("aborted", aborted["stage"])
            spool = secure_path(kb, "spool", receipt["batch_id"])
            old = (now - timedelta(hours=100)).timestamp()
            os.utime(spool, (old, old))
            self.assertEqual(1, gc_spool(kb, ttl_hours=24, now=now)["removed"])


if __name__ == "__main__":
    unittest.main()
