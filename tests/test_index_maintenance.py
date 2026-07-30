import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "index_maintenance",
    ROOT / "bin" / "index.py",
)
assert SPEC is not None and SPEC.loader is not None
INDEX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INDEX)


class IndexMaintenanceTest(unittest.TestCase):
    def _make_kb(self, root: Path) -> Path:
        kb = root / "kb"
        (kb / "knowledge" / "people").mkdir(parents=True)
        (kb / "raw_data").mkdir()
        (kb / "sources").mkdir()
        (kb / "knowledge" / "people" / "person-alice.md").write_text(
            "---\n"
            "id: person-alice\n"
            "type: person\n"
            "title: Alice\n"
            "updated: 2026-07-30\n"
            "---\n",
            encoding="utf-8",
        )
        (kb / "INDEX.md").write_text("# stale\n", encoding="utf-8")
        return kb

    def test_rebuild_dry_run_apply_and_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            kb = self._make_kb(Path(tmp))
            before = (kb / "INDEX.md").read_bytes()

            preview = INDEX.rebuild(kb, dry_run=True)
            self.assertEqual(preview["status"], "would_change")
            self.assertTrue(preview["changed"])
            self.assertEqual((kb / "INDEX.md").read_bytes(), before)
            self.assertFalse(preview["journal_written"])
            self.assertFalse(preview["git_commit_created"])

            applied = INDEX.rebuild(kb, dry_run=False)
            self.assertEqual(applied["status"], "rebuilt")
            self.assertTrue(applied["changed"])
            self.assertIn("person-alice", (kb / "INDEX.md").read_text(encoding="utf-8"))

            unchanged = INDEX.rebuild(kb, dry_run=False)
            self.assertEqual(unchanged["status"], "unchanged")
            self.assertFalse(unchanged["changed"])

    def test_rebuild_rejects_invalid_kb(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(INDEX.IndexMaintenanceError) as raised:
                INDEX.rebuild(Path(tmp), dry_run=True)
            self.assertEqual(raised.exception.code, "INDEX_KB_INVALID")

    @mock.patch.object(INDEX, "_invoke_rebuild")
    def test_rebuild_reports_preview_failure(self, invoke):
        invoke.return_value = subprocess.CompletedProcess(
            args=["rebuild-index.py"],
            returncode=1,
            stdout=b"",
            stderr=b"preview failed",
        )
        with tempfile.TemporaryDirectory() as tmp:
            kb = self._make_kb(Path(tmp))
            with self.assertRaises(INDEX.IndexMaintenanceError) as raised:
                INDEX.rebuild(kb, dry_run=True)
            self.assertEqual(raised.exception.code, "INDEX_REBUILD_FAILED")

    @mock.patch.object(INDEX, "_invoke_rebuild")
    def test_rebuild_verifies_applied_bytes(self, invoke):
        invoke.side_effect = [
            subprocess.CompletedProcess(
                args=["rebuild-index.py"],
                returncode=0,
                stdout=b"# expected\n",
                stderr=b"",
            ),
            subprocess.CompletedProcess(
                args=["rebuild-index.py"],
                returncode=0,
                stdout=b"",
                stderr=b"",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            kb = self._make_kb(Path(tmp))
            with self.assertRaises(INDEX.IndexMaintenanceError) as raised:
                INDEX.rebuild(kb, dry_run=False)
            self.assertEqual(raised.exception.code, "INDEX_REBUILD_VERIFY_FAILED")


if __name__ == "__main__":
    unittest.main()
