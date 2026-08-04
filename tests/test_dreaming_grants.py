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

from dreaming_grants import get_im_grant, set_im_grant  # noqa: E402
from dreaming_state import DreamingError, secure_path  # noqa: E402


class DreamingGrantTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        return kb

    def test_all_visible_requires_ack_and_increments_revision(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            with self.assertRaises(DreamingError) as caught:
                set_im_grant(
                    kb,
                    mode="all_visible",
                    persist_finding=False,
                    acknowledge_all_visible=False,
                )
            self.assertEqual("DREAMING_GRANT_ACK_REQUIRED", caught.exception.code)
            value = set_im_grant(
                kb,
                mode="all_visible",
                persist_finding=True,
                acknowledge_all_visible=True,
                now=datetime(2026, 8, 4, tzinfo=timezone.utc),
            )
            self.assertEqual(1, value["revision"])
            self.assertTrue(value["persist_finding"])
            self.assertEqual(value["revision"], get_im_grant(kb)["revision"])

    def test_downgrade_removes_discovery_but_keeps_monitored(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            set_im_grant(
                kb,
                mode="all_visible",
                persist_finding=False,
                acknowledge_all_visible=True,
            )
            for batch_id, lane in (("EB-discovery", "discovery"), ("EB-monitored", "monitored")):
                batch = secure_path(kb, "batches", batch_id)
                spool = secure_path(kb, "spool", batch_id)
                batch.mkdir(parents=True)
                spool.mkdir(parents=True)
                (batch / "manifest.json").write_text(
                    json.dumps({"source": {"lane": lane}}),
                    encoding="utf-8",
                )
                (spool / "content.json").write_text("{}", encoding="utf-8")
            result = set_im_grant(
                kb,
                mode="monitored",
                persist_finding=False,
                acknowledge_all_visible=False,
            )
            self.assertGreater(result["cleanup"]["directories"], 0)
            self.assertFalse(secure_path(kb, "batches", "EB-discovery").exists())
            self.assertFalse(secure_path(kb, "spool", "EB-discovery").exists())
            self.assertTrue(secure_path(kb, "batches", "EB-monitored").exists())
            self.assertTrue(secure_path(kb, "spool", "EB-monitored").exists())


if __name__ == "__main__":
    unittest.main()
