import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_scheduler import enable  # noqa: E402
from report_automation import configure  # noqa: E402


class DreamingI5CliTests(unittest.TestCase):
    def test_owner_migration_and_report_packet(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            now = datetime(2026, 8, 4, tzinfo=timezone.utc)
            enable(
                kb,
                harness="test",
                timezone_name="Asia/Shanghai",
                acknowledge_machine_runtime=True,
                acknowledge_capability_tour=True,
                acknowledge_schedule=True,
                now=now,
            )
            configure(
                kb,
                harness="test",
                timezone_name="Asia/Shanghai",
                environment="local",
                daily_schedule="工作日 20:30",
                weekly_schedule="周一 09:30",
                now=now,
            )
            released = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "report-automation.py"),
                    "release-owner",
                    "--kb",
                    str(kb),
                    "--acknowledge-tasks-stopped",
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, released.returncode, released.stdout)
            migrated = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "manage-reports",
                    "--kb",
                    str(kb),
                    "--enabled",
                    "true",
                    "--acknowledge-owner-released",
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, migrated.returncode, migrated.stdout)
            self.assertTrue(json.loads(migrated.stdout)["manage_reports"])
            prepared = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "report",
                    "prepare",
                    "--kb",
                    str(kb),
                    "--kind",
                    "morning",
                    "--period",
                    "2026-08-04",
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, prepared.returncode, prepared.stdout)
            packet = kb / json.loads(prepared.stdout)["packet_path"]
            self.assertTrue(packet.is_file())

            document = root / "report.json"
            document.write_text(
                json.dumps(
                    {
                        "schema_version": "byteworker-report-document/v1",
                        "kind": "morning",
                        "period": "2026-08-04",
                        "title": "晨报 · 2026-08-04",
                        "generated_at": "2026-08-04 10:00",
                        "window": {
                            "start": "2026-08-03 20:30",
                            "end": "2026-08-04 10:00",
                            "timezone": "Asia/Shanghai",
                        },
                        "coverage": {
                            "status": "covered",
                            "notes": [],
                        },
                        "message_summary": "晨报摘要：" + "重要信息需要关注。" * 40,
                        "sections": {
                            "highlights": [],
                            "changes": [],
                            "risks": [],
                            "confirmations": [],
                            "todos": [],
                        },
                        "sources": [],
                        "manual_notes": "",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            rendered = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "report",
                    "render",
                    "--kb",
                    str(kb),
                    "--input",
                    str(document),
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, rendered.returncode, rendered.stdout)
            rendered_value = json.loads(rendered.stdout)
            self.assertTrue(Path(rendered_value["html_path"]).is_file())
            self.assertTrue((kb / rendered_value["manifest_path"]).is_file())


if __name__ == "__main__":
    unittest.main()
