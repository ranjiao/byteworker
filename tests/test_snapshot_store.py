import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from snapshot_store import (  # noqa: E402
    diff_current_against_kb,
    list_snapshots,
    load_snapshot,
)
from source_capture import SourceCaptureError  # noqa: E402


class SnapshotStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.kb = Path(self.temp.name)
        (self.kb / "raw_data").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_raw(
        self,
        *,
        name: str,
        ingested: str,
        source_type: str = "meego",
        source_uid: str = "meego:project:view",
        records=None,
        snapshot_overrides=None,
        body: str | None = None,
        digest_status: str = "digested",
        wrapper: bool = False,
    ) -> Path:
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": source_type,
            "source_uid": source_uid,
            "records": records if records is not None else [],
            **(snapshot_overrides or {}),
        }
        value = {"snapshot": snapshot} if wrapper else snapshot
        if body is None:
            body = (
                "## 完整快照\n\n```json\n"
                + json.dumps(value, ensure_ascii=False)
                + "\n```\n"
            )
        status_line = (
            f"digest_status: {digest_status}\n" if digest_status else ""
        )
        path = self.kb / "raw_data" / f"{name}.md"
        path.write_text(
            (
                "---\n"
                f"raw_id: raw-{name}\n"
                f"ingested: {ingested}\n"
                f"source_type: {source_type}\n"
                f"source_uid: {source_uid}\n"
                f"source_url: https://example.test/{name}\n"
                f"source_title: {name}\n"
                f"{status_line}"
                "---\n\n"
                f"{body}"
            ),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def capture(records, *, source_uid="meego:project:view"):
        snapshot = {
            "schema_version": "byteworker-source-snapshot/v1",
            "source_type": "meego",
            "source_uid": source_uid,
            "records": records,
        }
        return {
            "content_hash": "sha256:current",
            "snapshot": snapshot,
        }

    def test_latest_and_explicit_history_are_deterministic(self):
        self.write_raw(
            name="older",
            ingested="2026-07-28",
            records=[{"work_item_id": "1", "status": "todo"}],
        )
        self.write_raw(
            name="latest",
            ingested="2026-07-29T10:00:00+08:00",
            records=[{"work_item_id": "1", "status": "done"}],
            wrapper=True,
        )

        history = list_snapshots(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
        )
        latest = load_snapshot(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
        )
        older = load_snapshot(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
            history_index=1,
        )
        by_raw_id = load_snapshot(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
            raw_id="raw-older",
        )

        self.assertEqual(["raw-latest", "raw-older"], [
            item.provenance.raw_id for item in history
        ])
        self.assertEqual("raw-latest", latest.provenance.raw_id)
        self.assertEqual("raw-older", older.provenance.raw_id)
        self.assertEqual("raw-older", by_raw_id.provenance.raw_id)
        self.assertEqual(
            "raw_data/latest.md",
            latest.provenance.raw_path,
        )
        self.assertTrue(latest.snapshot_hash.startswith("sha256:"))

    def test_legacy_snapshot_identity_is_inferred_from_frontmatter(self):
        self.write_raw(
            name="legacy",
            ingested="2026-07-29",
            snapshot_overrides={"source_type": "", "source_uid": ""},
        )

        persisted = load_snapshot(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
        )

        self.assertTrue(persisted.identity_inferred)
        self.assertEqual("meego", persisted.snapshot["source_type"])
        self.assertEqual(
            "meego:project:view",
            persisted.snapshot["source_uid"],
        )

    def test_git_backed_store_rejects_uncommitted_raw(self):
        subprocess.run(["git", "init", "-q"], cwd=self.kb, check=True)
        subprocess.run(
            ["git", "config", "user.email", "snapshot@example.test"],
            cwd=self.kb,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Snapshot Tests"],
            cwd=self.kb,
            check=True,
        )
        path = self.write_raw(
            name="tracked",
            ingested="2026-07-29",
            records=[{"work_item_id": "1"}],
        )
        subprocess.run(
            ["git", "add", str(path.relative_to(self.kb))],
            cwd=self.kb,
            check=True,
        )
        subprocess.run(["git", "commit", "-qm", "snapshot"], cwd=self.kb, check=True)
        persisted = load_snapshot(
            self.kb,
            source_type="meego",
            source_uid="meego:project:view",
        )
        self.assertEqual("raw-tracked", persisted.provenance.raw_id)

        path.write_text(
            path.read_text(encoding="utf-8").replace(
                '"work_item_id": "1"',
                '"work_item_id": "2"',
            ),
            encoding="utf-8",
        )
        with self.assertRaises(SourceCaptureError) as caught:
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
            )
        self.assertEqual(
            "SOURCE_SNAPSHOT_UNCOMMITTED_RAW",
            caught.exception.code,
        )

    def test_first_capture_returns_baseline(self):
        current = self.capture(
            [{"work_item_id": "1", "status": "todo"}]
        )

        result = diff_current_against_kb(current, self.kb)

        self.assertEqual(1, result["summary"]["baseline"])
        self.assertIsNone(result["previous_raw"])

    def test_diff_uses_latest_persisted_snapshot_and_reports_raw(self):
        self.write_raw(
            name="previous",
            ingested="2026-07-28",
            records=[{"work_item_id": "1", "status": "todo"}],
        )
        self.write_raw(
            name="latest",
            ingested="2026-07-29",
            records=[{"work_item_id": "1", "status": "doing"}],
        )
        current = self.capture(
            [{"work_item_id": "1", "status": "done"}]
        )

        result = diff_current_against_kb(current, self.kb)

        self.assertEqual(1, result["summary"]["changed"])
        self.assertEqual(["status"], result["changes"][0]["changed_paths"])
        self.assertEqual("doing", result["changes"][0]["before"]["status"])
        self.assertEqual("raw-latest", result["previous_raw"]["raw_id"])

    def test_malformed_json_fails_closed(self):
        self.write_raw(
            name="bad-json",
            ingested="2026-07-29",
            body="```json\n{\"broken\":\n```\n",
        )

        with self.assertRaisesRegex(
            SourceCaptureError,
            "结构化 JSON 无法解析",
        ):
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
            )

    def test_unclosed_json_fence_fails_closed(self):
        self.write_raw(
            name="unclosed",
            ingested="2026-07-29",
            body="```json\n{}\n",
        )

        with self.assertRaisesRegex(SourceCaptureError, "code fence 未闭合"):
            list_snapshots(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
            )

    def test_snapshot_identity_mismatch_fails_closed(self):
        self.write_raw(
            name="mismatch",
            ingested="2026-07-29",
            snapshot_overrides={"source_uid": "meego:other:view"},
        )

        with self.assertRaisesRegex(SourceCaptureError, "不一致"):
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
            )

    def test_incomplete_raw_fails_closed(self):
        self.write_raw(
            name="pending",
            ingested="2026-07-29",
            digest_status="pending",
        )

        with self.assertRaisesRegex(SourceCaptureError, "尚未完成 digest"):
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
            )

    def test_explicit_missing_history_fails(self):
        with self.assertRaisesRegex(SourceCaptureError, "history_index=1"):
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
                history_index=1,
            )

    def test_explicit_source_uid_mismatch_fails(self):
        current = self.capture([])

        with self.assertRaisesRegex(SourceCaptureError, "显式 source_uid"):
            diff_current_against_kb(
                current,
                self.kb,
                source_uid="meego:other:view",
            )

    def test_raw_id_selector_fails_on_source_mismatch(self):
        self.write_raw(
            name="other",
            ingested="2026-07-29",
            source_uid="meego:other:view",
        )

        with self.assertRaisesRegex(
            SourceCaptureError,
            "frontmatter 与请求的来源身份不一致",
        ):
            load_snapshot(
                self.kb,
                source_type="meego",
                source_uid="meego:project:view",
                raw_id="raw-other",
            )


if __name__ == "__main__":
    unittest.main()
