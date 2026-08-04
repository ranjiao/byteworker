import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from context_view import INTENT_SECTIONS  # noqa: E402
from doctor import EXPECTED_DIRS, scan  # noqa: E402


class InboxRemovalTests(unittest.TestCase):
    def test_tombstone_has_no_filesystem_or_kb_side_effects(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            legacy = kb / "reports" / "im" / "2026-08-03.md"
            legacy.parent.mkdir(parents=True)
            legacy.write_bytes(b"# historical inbox report\n")
            before = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            env = {
                "HOME": str(root / "home"),
                "PATH": "/nonexistent",
                "BYTEWORKER_KB": str(kb),
                "BYTEWORKER_DREAMING_STATE_DIR": str(root / "dreaming"),
            }

            completed = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "inbox.py"), "yesterday"],
                cwd=root,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(2, completed.returncode)
            self.assertEqual("", completed.stderr)
            self.assertEqual(
                "INBOX_REMOVED",
                json.loads(completed.stdout)["error"]["code"],
            )
            after = {
                path.relative_to(root): path.read_bytes()
                for path in root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(before, after)

    def test_no_active_inbox_route_writer_or_template_remains(self):
        routes = json.loads(
            (ROOT / "references" / "workflow-routes.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("inbox", routes["workflows"])
        self.assertNotIn("inbox", routes["budgets"])
        self.assertNotIn("inbox", INTENT_SECTIONS)
        self.assertNotIn("reports/im", EXPECTED_DIRS)
        for relative in (
            "bin/im-inbox-summary.sh",
            "references/im-inbox-summary.md",
            "templates/report-im.md",
        ):
            self.assertFalse((ROOT / relative).exists(), relative)

    def test_doctor_preserves_legacy_reports_im_byte_for_byte(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            kb = Path(temporary)
            report = kb / "reports" / "im" / "2026-08-03.md"
            report.parent.mkdir(parents=True)
            original = (
                b"# IM Inbox Summary\n\n"
                b"Historical user-edited content. [S1]\n\n"
                b"## References\n\n- [S1] legacy\n"
            )
            report.write_bytes(original)

            result = scan(kb, ROOT)

            self.assertEqual(original, report.read_bytes())
            self.assertFalse(
                any(
                    item.code == "LAYOUT_MISSING_DIRECTORY"
                    and item.path == "reports/im"
                    for item in result.findings
                )
            )


if __name__ == "__main__":
    unittest.main()
