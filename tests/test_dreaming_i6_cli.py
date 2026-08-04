import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DreamingI6CliTests(unittest.TestCase):
    def test_disabled_foreground_once_has_no_background_side_effect(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            fake = root / "lark-cli"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:3] == ['auth', 'status']:\n"
                " print(json.dumps({'ok':True,'data':{'identities':{'user':{'openId':'ou_me'}}}}))\n"
                "else:\n"
                " print(json.dumps({'ok':True,'data':{'messages':[],'has_more':False,'page_token':''}}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            once = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "process",
                    "once",
                    "--kb",
                    str(kb),
                    "--source",
                    "im",
                    "--mode",
                    "all_visible",
                    "--acknowledge-all-visible",
                    "--start",
                    "2020-01-01T00:00:00+00:00",
                    "--end",
                    "2020-01-01T01:00:00+00:00",
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
                env={**os.environ, "BYTEWORKER_LARK_CLI_BIN": str(fake)},
            )
            self.assertEqual(0, once.returncode, once.stdout)
            payload = json.loads(once.stdout)
            self.assertTrue(payload["foreground"])
            self.assertNotIn("token", payload)
            status = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "status",
                    "--kb",
                    str(kb),
                ],
                text=True,
                stdout=subprocess.PIPE,
                check=False,
            )
            status_value = json.loads(status.stdout)
            self.assertFalse(status_value["enabled"])
            self.assertEqual("off", status_value["grants"]["im"]["mode"])
            self.assertNotIn("foreground_sessions", status_value)


if __name__ == "__main__":
    unittest.main()
