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
        for directory in (
            "people",
            "projects",
            "areas",
            "orgs",
            "events",
            "decisions",
            "readings",
        ):
            (kb / "knowledge" / directory).mkdir(parents=True)
        for directory in ("raw_data", "sources", "provenance", "journal"):
            (kb / directory).mkdir()
        for directory in ("daily", "weekly", "im"):
            (kb / "reports" / directory).mkdir(parents=True)
        for name in ("context.md", "todo.md", "dashboard.md"):
            (kb / name).write_text(f"# {name}\n", encoding="utf-8")
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
        subprocess.run(["git", "init", "-q"], cwd=kb, check=True)
        subprocess.run(
            ["git", "config", "user.email", "index@example.test"],
            cwd=kb,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Index Test"],
            cwd=kb,
            check=True,
        )
        subprocess.run(["git", "add", "."], cwd=kb, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=kb, check=True)
        return kb

    def test_rebuild_dry_run_apply_and_unchanged(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
            self.assertTrue(applied["journal_written"])
            self.assertTrue(applied["git_commit_created"])
            self.assertTrue(applied["commit"])
            self.assertIn("person-alice", (kb / "INDEX.md").read_text(encoding="utf-8"))

            unchanged = INDEX.rebuild(kb, dry_run=False)
            self.assertEqual(unchanged["status"], "unchanged")
            self.assertFalse(unchanged["changed"])

    def test_rebuild_rejects_invalid_kb(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
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
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            kb = self._make_kb(Path(tmp))
            with self.assertRaises(INDEX.IndexMaintenanceError) as raised:
                INDEX.rebuild(kb, dry_run=True)
            self.assertEqual(raised.exception.code, "INDEX_REBUILD_FAILED")

    @mock.patch.object(INDEX, "run_postflight")
    def test_rebuild_surfaces_transaction_failure(self, postflight):
        postflight.return_value = mock.Mock(
            commit="",
            reasons=["forced transaction failure"],
        )
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            kb = self._make_kb(Path(tmp))
            with self.assertRaises(INDEX.IndexMaintenanceError) as raised:
                INDEX.rebuild(kb, dry_run=False)
            self.assertEqual(raised.exception.code, "INDEX_REBUILD_FAILED")
            self.assertIn("forced transaction failure", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
