import json
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"

if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from update_state import (  # noqa: E402
    empty_state,
    load_state,
    mark_postflight_pending,
    postflight_due,
    record_failure,
    record_postflight_failure,
    record_postflight_success,
    record_success,
    update_due,
    write_state,
)


class UpdateStateTests(unittest.TestCase):
    def test_success_uses_last_success_for_weekly_throttle(self):
        state = record_success(empty_state(), now=100, commit="abc")
        self.assertFalse(update_due(state, now=200, interval=604800))
        self.assertTrue(update_due(state, now=604900, interval=604800))
        self.assertEqual("abc", state["last_checked_commit"])

    def test_failure_uses_short_exponential_retry_not_weekly_interval(self):
        state = record_success(empty_state(), now=100, commit="abc")
        state = record_failure(
            state,
            now=604900,
            code="fetch-failed",
            retry_base=60,
            retry_max=600,
        )
        self.assertFalse(update_due(state, now=604959, interval=604800))
        self.assertTrue(update_due(state, now=604960, interval=604800))
        state = record_failure(
            state,
            now=604960,
            code="fetch-failed",
            retry_base=60,
            retry_max=600,
        )
        self.assertEqual(605080, state["next_retry_at"])

    def test_legacy_attempt_stamp_does_not_claim_a_successful_check(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            legacy = root / ".last-update-check"
            legacy.write_text("100\n", encoding="utf-8")
            state = load_state(root / ".update-state.json", legacy)
            self.assertEqual(100, state["last_attempt_at"])
            self.assertEqual(0, state["last_success_at"])
            self.assertTrue(update_due(state, now=101, interval=604800))

    def test_postflight_has_an_independent_retry_state(self):
        state = mark_postflight_pending(empty_state(), commit="next")
        self.assertTrue(postflight_due(state, now=100))
        state = record_postflight_failure(
            state,
            now=100,
            code="exit-2",
            retry_base=30,
            retry_max=300,
        )
        self.assertFalse(postflight_due(state, now=129))
        self.assertTrue(postflight_due(state, now=130))
        state = record_postflight_success(state)
        self.assertFalse(state["postflight_pending"])
        self.assertFalse(postflight_due(state, now=1000))

    def test_state_write_is_json_round_trip(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            path = Path(temporary) / "state.json"
            expected = record_success(empty_state(), now=123, commit="def")
            write_state(path, expected)
            self.assertEqual(expected, load_state(path))
            self.assertEqual(1, json.loads(path.read_text(encoding="utf-8"))["version"])


if __name__ == "__main__":
    unittest.main()
