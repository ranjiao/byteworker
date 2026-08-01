from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from kb_write_txn import kb_write_lock  # noqa: E402


class KbWriteTransactionTests(unittest.TestCase):
    def test_all_durable_writers_use_one_lock_contract(self):
        for relative in (
            "lib/digest_txn.py",
            "lib/source_profiles.py",
            "lib/provenance_backfill.py",
            "lib/update_postflight.py",
            "lib/kb_mutation.py",
            "bin/todo.py",
        ):
            with self.subTest(path=relative):
                text = (ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("kb_write_lock", text)
                self.assertNotIn("byteworker-digest.lock", text)
                self.assertNotIn("byteworker-source-profile.lock", text)
                self.assertNotIn("byteworker-provenance.lock", text)

    def test_shared_lock_serializes_independent_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            kb = Path(temporary)
            (kb / ".git").mkdir()
            marker = kb / "entered"
            code = (
                "import sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(LIB)!r})\n"
                "from kb_write_txn import kb_write_lock\n"
                f"kb=Path({str(kb)!r})\n"
                "with kb_write_lock(kb):\n"
                f" Path({str(marker)!r}).write_text('entered')\n"
            )
            with kb_write_lock(kb):
                process = subprocess.Popen([sys.executable, "-c", code])
                time.sleep(0.2)
                self.assertFalse(marker.exists())
            process.wait(timeout=5)
            self.assertEqual(0, process.returncode)
            self.assertEqual("entered", marker.read_text())


if __name__ == "__main__":
    unittest.main()
