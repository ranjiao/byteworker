from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_collection import prepare_foreground_im_batch, prepare_im_batch  # noqa: E402
from dreaming_grants import set_im_grant  # noqa: E402
from dreaming_state import DreamingError, load_state_unlocked, state_lock  # noqa: E402


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
                    "content": "hello",
                },
                {
                    "message_id": "om_1",
                    "chat_id": "oc_p2p",
                    "chat_type": "p2p",
                    "create_time": "2026-08-04T00:10:00+00:00",
                    "sender": {"id": "ou_sender"},
                    "content": "hello",
                },
            ],
            "coverage": {
                "status": "best_effort",
                "gaps": [
                    {
                        "kind": "window_budget",
                        "start": start,
                        "end": end,
                    }
                ],
            },
        }


class DreamingCollectionTests(unittest.TestCase):
    def make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / ".git" / "info").mkdir(parents=True)
        set_im_grant(
            kb,
            mode="all_visible",
            persist_finding=False,
            acknowledge_all_visible=True,
        )
        return kb

    def test_prepare_discovery_dedupes_and_records_split_gap(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            result = prepare_im_batch(
                kb,
                start="2026-08-04T00:00:00+00:00",
                end="2026-08-04T01:00:00+00:00",
                collector=FakeCollector(),
                now=now,
            )
            self.assertEqual("discovery", result["lane"])
            self.assertEqual(1, result["item_count"])
            self.assertEqual("best_effort", result["coverage"]["status"])
            self.assertEqual(2, len(result["coverage"]["gaps"]))
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            self.assertIn(result["batch_id"], state["gaps"])

    def test_future_or_invalid_window_is_rejected(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 8, 4, 2, tzinfo=timezone.utc)
            for start, end in (
                ("2026-08-04T03:00:00+00:00", "2026-08-04T04:00:00+00:00"),
                ("2026-08-04T01:00:00+00:00", "2026-08-04T00:00:00+00:00"),
            ):
                with self.subTest(start=start, end=end):
                    with self.assertRaises(DreamingError):
                        prepare_im_batch(
                            kb,
                            start=start,
                            end=end,
                            collector=FakeCollector(),
                            now=now,
                        )

    def test_background_collection_skips_old_windows_and_clips_crossing_windows(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = self.make_kb(Path(temporary))
            now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
            old = (now - timedelta(days=30)).isoformat()
            older_end = (now - timedelta(days=29)).isoformat()
            with self.assertRaises(DreamingError) as caught:
                prepare_im_batch(kb, start=old, end=older_end, collector=object(), now=now)
            self.assertEqual("DREAMING_IM_WINDOW_EXPIRED", caught.exception.code)
            collector = Mock(wraps=FakeCollector())
            recent = prepare_im_batch(kb, start=old, end=now.isoformat(), collector=collector, now=now)
            cutoff = (now - timedelta(days=14)).isoformat()
            collector.collect_discovery.assert_called_once_with(start=cutoff, end=now.isoformat())
            self.assertEqual(cutoff, recent["window"]["start"])
            self.assertEqual({"start": old, "end": cutoff, "reason": "backfill_age_limit"}, recent["skipped_window"])
            with state_lock(kb):
                state = load_state_unlocked(kb, now)
            self.assertIn(recent["skipped_window"], state["gaps"][recent["batch_id"]]["windows"])
            foreground = prepare_foreground_im_batch(
                kb, start=old, end=older_end, mode="all_visible", acknowledge_all_visible=True,
                collector=collector, now=now,
            )
            collector.collect_discovery.assert_called_with(start=old, end=older_end)
            self.assertEqual(old, foreground["window"]["start"])


if __name__ == "__main__":
    unittest.main()
