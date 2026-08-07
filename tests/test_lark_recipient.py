import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from dreaming_state import DreamingError  # noqa: E402
from lark_recipient import resolve_lark_recipient  # noqa: E402


class LarkRecipientTests(unittest.TestCase):
    def response(self, users, *, has_more=False):
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {"data": {"users": users, "has_more": has_more}},
                ensure_ascii=False,
            ),
            stderr="",
        )

    def test_open_id_is_accepted_without_lookup(self):
        with mock.patch("lark_recipient.subprocess.run") as run:
            resolved = resolve_lark_recipient("ou_test")
        self.assertEqual(
            {
                "recipient_id": "ou_test",
                "recipient_key": "ou_test",
                "display_name": "",
            },
            resolved,
        )
        run.assert_not_called()

    @mock.patch("lark_recipient.subprocess.run")
    def test_username_resolves_by_enterprise_email_prefix(self, run):
        run.return_value = self.response(
            [
                {
                    "open_id": "ou_ranjiao",
                    "localized_name": "冉娇",
                    "enterprise_email": "ranjiao@example.com",
                    "department": "Example-Team",
                }
            ]
        )
        resolved = resolve_lark_recipient("ranjiao", binary="lark-cli")
        self.assertEqual("ou_ranjiao", resolved["recipient_id"])
        self.assertEqual("ranjiao", resolved["recipient_key"])
        self.assertEqual("冉娇", resolved["display_name"])
        command = run.call_args.args[0]
        self.assertEqual(
            [
                "lark-cli",
                "contact",
                "+search-user",
                "--query",
                "ranjiao",
                "--exclude-external-users",
                "--as",
                "user",
            ],
            command,
        )

    @mock.patch("lark_recipient.subprocess.run")
    def test_ambiguous_username_is_rejected(self, run):
        run.return_value = self.response(
            [
                {
                    "open_id": "ou_a",
                    "localized_name": "A",
                    "enterprise_email": "alice@example.com",
                },
                {
                    "open_id": "ou_b",
                    "localized_name": "B",
                    "enterprise_email": "bob@example.com",
                },
            ]
        )
        with self.assertRaises(DreamingError) as caught:
            resolve_lark_recipient("example")
        self.assertEqual("DREAMING_RECIPIENT_AMBIGUOUS", caught.exception.code)

    @mock.patch("lark_recipient.subprocess.run")
    def test_lookup_failure_is_bounded(self, run):
        run.return_value = SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="provider secret detail",
        )
        with self.assertRaises(DreamingError) as caught:
            resolve_lark_recipient("ranjiao")
        self.assertEqual("DREAMING_RECIPIENT_LOOKUP_FAILED", caught.exception.code)
        self.assertNotIn("provider secret", str(caught.exception))

    def test_dreaming_cli_accepts_username_and_persists_open_id(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
            root = Path(temporary)
            kb = root / "kb"
            (kb / ".git" / "info").mkdir(parents=True)
            fake = root / "lark-cli"
            fake.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'data': {'users': [{"
                "'open_id': 'ou_ranjiao', "
                "'localized_name': 'Ranjiao', "
                "'enterprise_email': 'ranjiao@example.com'"
                "}], 'has_more': False}}))\n",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "bin" / "dreaming.py"),
                    "configure",
                    "--kb",
                    str(kb),
                    "--timezone",
                    "Asia/Shanghai",
                    "--lark-delivery-enabled",
                    "true",
                    "--lark-recipient",
                    "ranjiao",
                ],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env={**os.environ, "BYTEWORKER_LARK_CLI_BIN": str(fake)},
            )
        self.assertEqual(0, completed.returncode, completed.stdout)
        delivery = json.loads(completed.stdout)["report_delivery"]["lark_bot"]
        self.assertEqual("ranjiao", delivery["recipient_key"])
        self.assertEqual("ou_ranjiao", delivery["recipient_id"])


if __name__ == "__main__":
    unittest.main()
