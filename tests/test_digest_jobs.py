import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from digest_jobs import (  # noqa: E402
    DigestJobError,
    _parse_time,
    cancel_job,
    create_job,
    job_status,
    lease_next,
    list_jobs,
    mark_page,
    reconcile_job,
)


def selection():
    return {
        "schema_version": "byteworker-wiki-candidate-selection/v1",
        "space_id": "space-1",
        "space_url": "https://tenant.larkoffice.com/wiki/home",
        "root_node_token": "topic",
        "tree_hash": "sha256:tree",
        "pages": [
            {
                "document_id": "doc-1",
                "node_token": "node-1",
                "title": "页面一",
                "url": "https://tenant.larkoffice.com/wiki/node-1",
                "updated_at": "2026-07-01T00:00:00Z",
                "path_titles": ["主题", "页面一"],
            },
            {
                "document_id": "doc-2",
                "node_token": "node-2",
                "title": "页面二",
                "url": "https://tenant.larkoffice.com/wiki/node-2",
                "updated_at": "2026-07-02T00:00:00Z",
                "path_titles": ["主题", "页面二"],
            },
        ],
    }


class DigestJobTests(unittest.TestCase):
    def test_error_and_time_helpers_are_safe(self):
        error = DigestJobError(
            "CODE",
            "message",
            hint="hint",
            details={"count": 1},
        )
        self.assertEqual("hint", error.as_dict()["hint"])
        self.assertEqual(1, error.as_dict()["details"]["count"])
        self.assertIsNone(_parse_time(None))
        self.assertIsNone(_parse_time("bad"))
        self.assertIsNone(_parse_time("2026-01-01T00:00:00"))

    def test_normal_kb_use_does_not_create_job_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            self.assertEqual([], list_jobs(kb))
            self.assertFalse((kb / "state").exists())

    def test_create_lease_mark_and_resume_across_loads(self):
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            created = create_job(kb, selection(), batch_size=1, now=now)
            self.assertEqual("WJ-20260730-001", created["job_id"])
            self.assertEqual(2, created["page_count"])
            first = lease_next(
                kb,
                created["job_id"],
                limit=1,
                lease_owner="session-a",
                now=now,
            )
            self.assertEqual(["doc-1"], [item["document_id"] for item in first["pages"]])
            marked = mark_page(
                kb,
                created["job_id"],
                document_id="doc-1",
                status="committed",
                raw_id="raw-1",
                commit="abc123",
                now=now + timedelta(minutes=1),
            )
            self.assertEqual("ready", marked["job_status"])
            second = lease_next(
                kb,
                created["job_id"],
                limit=1,
                lease_owner="session-b",
                now=now + timedelta(minutes=2),
            )
            self.assertEqual(["doc-2"], [item["document_id"] for item in second["pages"]])

    def test_expired_lease_is_recovered(self):
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            job_id = create_job(kb, selection(), now=now)["job_id"]
            lease_next(
                kb,
                job_id,
                limit=1,
                lease_owner="dead-session",
                lease_seconds=10,
                now=now,
            )
            recovered = lease_next(
                kb,
                job_id,
                limit=1,
                lease_owner="new-session",
                now=now + timedelta(seconds=11),
            )
            self.assertEqual("doc-1", recovered["pages"][0]["document_id"])
            self.assertEqual(2, recovered["pages"][0]["attempt"])

    def test_reconcile_recovers_digest_committed_before_job_mark(self):
        now = datetime(2026, 7, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            job_id = create_job(kb, selection(), now=now)["job_id"]
            raw = kb / "raw_data" / "raw-1.md"
            raw.parent.mkdir()
            raw.write_text(
                "---\n"
                "id: raw-1\n"
                "source_uid: doc-1\n"
                "digest_status: digested\n"
                "---\n# page\n",
                encoding="utf-8",
            )
            receipt = reconcile_job(kb, job_id, now=now)
            self.assertEqual(1, receipt["reconciled_count"])
            self.assertEqual(1, job_status(kb, job_id)["page_counts"]["committed"])

    def test_committed_requires_raw_id_and_cancel_is_terminal(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            job_id = create_job(kb, selection())["job_id"]
            with self.assertRaises(DigestJobError):
                mark_page(
                    kb,
                    job_id,
                    document_id="doc-1",
                    status="committed",
                )
            cancelled = cancel_job(kb, job_id)
            self.assertEqual("cancelled", cancelled["status"])
            self.assertEqual({"skipped": 2}, cancelled["page_counts"])
            with self.assertRaises(DigestJobError):
                lease_next(
                    kb,
                    job_id,
                    limit=1,
                    lease_owner="session",
                )

    def test_selection_rejects_duplicates_and_is_not_copied_verbatim(self):
        value = selection()
        value["pages"].append(dict(value["pages"][0]))
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(DigestJobError):
                create_job(Path(temporary), value)

    def test_selection_and_job_file_validation_fail_closed(self):
        invalid_selections = (
            {},
            {
                "schema_version": "byteworker-wiki-candidate-selection/v1",
                "pages": [],
            },
            {
                "schema_version": "byteworker-wiki-candidate-selection/v1",
                "pages": ["bad"],
            },
            {
                "schema_version": "byteworker-wiki-candidate-selection/v1",
                "pages": [{"document_id": "x"}],
            },
        )
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            for value in invalid_selections:
                with self.subTest(value=value):
                    with self.assertRaises(DigestJobError):
                        create_job(kb, value)
            for batch_size in (0, 51):
                with self.assertRaises(DigestJobError):
                    create_job(kb, selection(), batch_size=batch_size)
            with self.assertRaises(DigestJobError):
                job_status(kb, "invalid", limit=1)
            with self.assertRaises(DigestJobError):
                job_status(kb, "WJ-20260730-999", limit=1)

            root = kb / "state" / "digest_jobs"
            root.mkdir(parents=True)
            path = root / "WJ-20260730-999.json"
            for payload in ("not-json", "{}", '{"schema_version":"byteworker-digest-job/v1"}'):
                path.write_text(payload, encoding="utf-8")
                with self.assertRaises(DigestJobError):
                    job_status(kb, path.stem, limit=1)

    def test_invalid_transitions_and_terminal_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            job_id = create_job(kb, selection())["job_id"]
            with self.assertRaises(DigestJobError):
                job_status(kb, job_id, limit=0)
            for kwargs in (
                {"limit": 0, "lease_owner": "x"},
                {"limit": 1, "lease_owner": ""},
            ):
                with self.assertRaises(DigestJobError):
                    lease_next(kb, job_id, **kwargs)
            with self.assertRaises(DigestJobError):
                mark_page(
                    kb,
                    job_id,
                    document_id="missing",
                    status="skipped",
                )
            with self.assertRaises(DigestJobError):
                mark_page(
                    kb,
                    job_id,
                    document_id="doc-1",
                    status="pending",
                )
            mark_page(kb, job_id, document_id="doc-1", status="noop")
            with self.assertRaises(DigestJobError):
                mark_page(
                    kb,
                    job_id,
                    document_id="doc-1",
                    status="permanent_error",
                )
            mark_page(
                kb,
                job_id,
                document_id="doc-2",
                status="permanent_error",
            )
            self.assertEqual("failed", job_status(kb, job_id)["status"])
            with self.assertRaises(DigestJobError):
                lease_next(kb, job_id, limit=1, lease_owner="x")

    def test_waiting_user_completed_cancel_and_active_filter(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            blocked = create_job(kb, selection())["job_id"]
            mark_page(
                kb,
                blocked,
                document_id="doc-1",
                status="blocked_dependency",
            )
            self.assertEqual("waiting_user", job_status(kb, blocked)["status"])
            completed = create_job(kb, selection())["job_id"]
            for document_id in ("doc-1", "doc-2"):
                mark_page(
                    kb,
                    completed,
                    document_id=document_id,
                    status="noop",
                )
            self.assertEqual("completed", job_status(kb, completed)["status"])
            self.assertNotIn(
                completed,
                {item["job_id"] for item in list_jobs(kb, active_only=True)},
            )
            with self.assertRaises(DigestJobError):
                cancel_job(kb, completed)


if __name__ == "__main__":
    unittest.main()
