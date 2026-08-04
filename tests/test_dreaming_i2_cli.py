import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DreamingI2CliTests(unittest.TestCase):
    def test_grant_and_process_prepare_do_not_emit_message_content(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            fake = root / "lark-cli"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "if sys.argv[1:3] == ['auth', 'status']:\n"
                " print(json.dumps({'ok': True, 'data': {'identities': {'user': {'openId': 'ou_me'}}}}))\n"
                "else:\n"
                " print(json.dumps({'ok': True, 'data': {'messages': [{'message_id':'om_1','chat_id':'oc_1','chat_type':'p2p','create_time':'2020-01-01T00:10:00+00:00','sender':{'id':'ou_sender'},'content':'TOP SECRET'}], 'has_more': False, 'page_token': ''}}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            grant = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "grant",
                    "set-im",
                    "--kb",
                    str(kb),
                    "--mode",
                    "all_visible",
                    "--acknowledge-all-visible",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertEqual(0, grant.returncode, grant.stdout)
            prepared = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "process",
                    "prepare",
                    "--kb",
                    str(kb),
                    "--source",
                    "im",
                    "--start",
                    "2020-01-01T00:00:00+00:00",
                    "--end",
                    "2020-01-01T01:00:00+00:00",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={**os.environ, "BYTEWORKER_LARK_CLI_BIN": str(fake)},
            )
            self.assertEqual(0, prepared.returncode, prepared.stdout)
            payload = json.loads(prepared.stdout)
            self.assertEqual("collected", payload["stage"])
            self.assertEqual(1, payload["item_count"])
            self.assertNotIn("TOP SECRET", prepared.stdout)


if __name__ == "__main__":
    unittest.main()
